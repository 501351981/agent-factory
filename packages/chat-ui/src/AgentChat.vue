<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { ElMessage } from 'element-plus'
import { Document, RefreshRight, Service, Bottom } from '@element-plus/icons-vue'
import {
  cancelQueueItem,
  cancelRun,
  createSession,
  fetchAgent,
  fetchModels,
  fetchQueue,
  fetchSessionBoard,
  fetchTimeline,
  streamChat,
  streamResumeRun,
  streamRunEvents,
  type BoardArtifactDto,
  type ModelProfileDto,
  type QueueItemDto,
  type SkillDto,
} from './api'
import BoardPanel from './BoardPanel.vue'
import ChatComposer from './ChatComposer.vue'
import ChatTurnItem from './ChatTurnItem.vue'
import HitlCard from './HitlCard.vue'
import { timelineToTurns } from './timeline'
import type { ChatTurn } from './types'
import { themeToCssVars } from './uiTheme'

const props = withDefaults(
  defineProps<{
    baseUrl: string
    agentId: string
    sessionId?: string
    apiKey?: string
    userKey?: string
    locale?: string
    promptVars?: Record<string, string>
    clientDeviceId?: string
    /** Embed hosts typically hide the chrome header. */
    showHeader?: boolean
    /**
     * Who creates sessions when none is bound:
     * - auto: call POST /api/sessions inside chat-ui
     * - host: postMessage session_create_request; wait for host set_session
     */
    sessionCreateMode?: 'auto' | 'host'
  }>(),
  { showHeader: true, sessionCreateMode: 'auto' },
)

const emit = defineEmits<{
  (e: 'session', sessionId: string): void
  (e: 'session-create-request', payload: Record<string, unknown>): void
  (e: 'error', error: string): void
}>()

const turns = ref<ChatTurn[]>([])
const input = ref('')
const activeSessionId = ref<string | undefined>(props.sessionId)
const loading = ref(false)
const bootstrapping = ref(true)
const errorText = ref('')
const listRef = ref<HTMLElement | null>(null)
const composerRef = ref<{ focus: () => void | Promise<void>; setText: (text: string) => Promise<void> } | null>(null)
const activeRunId = ref<string | null>(null)
const abortRef = ref<AbortController | null>(null)
const userStopped = ref(false)
const contextHint = ref('')
const agentName = ref('')
const agentLogoUrl = ref('')
const welcomeTitle = ref('')
const welcomeSubtitle = ref('')
const suggestedPrompts = ref<string[]>([])
const uiTheme = ref<Record<string, string>>({})
const models = ref<ModelProfileDto[]>([])
const skills = ref<SkillDto[]>([])
const selectedModelProfileId = ref('')
/** Host mode: message waiting while parent creates a session. */
const pendingSendText = ref('')
const awaitingHostSession = ref(false)
/** When false, streaming won't yank the viewport (user scrolled up). */
const stickToBottom = ref(true)
const boardOpen = ref(false)
const boardLoading = ref(false)
const boardArtifacts = ref<BoardArtifactDto[]>([])
const boardError = ref('')
const boardFullscreen = ref(false)
const boardWidthPx = ref(420)
const boardResizing = ref(false)
const chatRootRef = ref<HTMLElement | null>(null)
const BOARD_WIDTH_MIN = 280
const BOARD_WIDTH_MAX_RATIO = 0.85
const CHAT_WIDTH_MIN = 280
/** Leave stick-to-bottom once farther than this (hysteresis with ENTER). */
const EXIT_STICK_PX = 120
/** Re-enter stick-to-bottom only when this close to the end. */
const ENTER_STICK_PX = 28
let pollTimer: ReturnType<typeof setInterval> | null = null
let boardResizeCleanup: (() => void) | null = null
/** Ignore scroll events caused by our own scrollToBottom. */
let programmaticScroll = false
let programmaticScrollTimer: ReturnType<typeof setTimeout> | null = null
/** Coalesce bursty stream deltas into one pin-to-bottom per frame. */
let scrollRaf: number | null = null
let lastTouchY: number | null = null

const displayName = computed(() => agentName.value || 'Agent 助手')
const sessionLabel = computed(() => {
  if (!activeSessionId.value) return '新对话'
  return `会话 ${activeSessionId.value.slice(0, 8)}`
})
const themeStyle = computed(() => themeToCssVars(uiTheme.value))
const emptyTitle = computed(() => welcomeTitle.value.trim() || '有什么可以帮忙的？')
const emptyDesc = computed(() => {
  const custom = welcomeSubtitle.value.trim()
  if (custom) return custom
  if (props.sessionCreateMode === 'host' && !activeSessionId.value) {
    return awaitingHostSession.value
      ? '正在等待业务侧创建会话…'
      : '发送消息后将请求业务侧创建会话；也可由宿主先创建再注入 sessionId'
  }
  return `向 ${displayName.value} 提问，Enter 发送，Shift+Enter 换行${
    skills.value.length ? '；输入 / 可选择技能' : ''
  }`
})
const emptyPrompts = computed(() =>
  suggestedPrompts.value.map((item) => item.trim()).filter(Boolean).slice(0, 12),
)

function resolveMediaUrl(url: string, baseUrl: string): string {
  const value = (url || '').trim()
  if (!value) return ''
  if (/^(https?:|data:|blob:)/i.test(value)) return value
  const base = (baseUrl || '').replace(/\/$/, '')
  return value.startsWith('/') ? `${base}${value}` : `${base}/${value}`
}

function modelStorageKey(agentId: string) {
  return `af.chat.modelProfileId.${agentId}`
}

function pickDefaultModelId(
  list: ModelProfileDto[],
  preferredId?: string | null,
): string {
  const enabled = list.filter((m) => m.enabled)
  if (!enabled.length) return ''
  if (preferredId && enabled.some((m) => m.id === preferredId)) return preferredId
  const stored =
    typeof localStorage !== 'undefined'
      ? localStorage.getItem(modelStorageKey(props.agentId))
      : null
  if (stored && enabled.some((m) => m.id === stored)) return stored
  const def = enabled.find((m) => m.is_default)
  return (def || enabled[0]).id
}

watch(selectedModelProfileId, (id) => {
  if (!id || typeof localStorage === 'undefined') return
  localStorage.setItem(modelStorageKey(props.agentId), id)
})

const queueItems = ref<QueueItemDto[]>([])
const enqueueing = ref(false)
const queueStripRef = ref<HTMLElement | null>(null)

const hasRunningTurn = computed(() => turns.value.some((t) => t.status === 'running'))
const hasWaitingHuman = computed(() =>
  turns.value.some(
    (t) => t.status === 'waiting_human' && (!!t.humanAsk || !!t.humanApproval),
  ),
)
/** The turn that currently needs ask/approval — docked above the composer. */
const waitingHitlTurn = computed(
  () =>
    turns.value.find(
      (t) => t.status === 'waiting_human' && (!!t.humanAsk || !!t.humanApproval),
    ) || null,
)
const boardBadge = computed(() => boardArtifacts.value.length)
const canSend = computed(
  () =>
    !!props.agentId &&
    !enqueueing.value &&
    !hasWaitingHuman.value &&
    !awaitingHostSession.value,
)
const showJumpToBottom = computed(
  () => !stickToBottom.value && turns.value.length > 0,
)
const composerPlaceholder = computed(() => {
  if (awaitingHostSession.value) return '正在等待业务侧创建会话…'
  if (hasWaitingHuman.value) return '请先完成下方确认/审批，或点停止取消'
  if (hasRunningTurn.value) return '回复进行中，可继续输入，发送后将排队'
  if (skills.value.length) return '输入消息，/ 选择技能，Enter 发送'
  return '输入消息，Enter 发送，Shift+Enter 换行'
})

function queuePreview(message: string, max = 18): string {
  const text = (message || '').replace(/\s+/g, ' ').trim()
  if (text.length <= max) return text
  return `${text.slice(0, max)}…`
}

watch(
  () => queueItems.value.length,
  async (len, prev) => {
    if (len > (prev || 0)) {
      await nextTick()
      const el = queueStripRef.value
      if (el) el.scrollLeft = el.scrollWidth
    }
  },
)

async function refreshQueue(sessionId?: string) {
  const id = sessionId || activeSessionId.value
  if (!id) {
    queueItems.value = []
    return
  }
  try {
    queueItems.value = await fetchQueue(props.baseUrl, id, props.apiKey)
  } catch {
    // keep previous
  }
}

async function removeQueued(itemId: string) {
  const sessionId = activeSessionId.value
  if (!sessionId) return
  try {
    await cancelQueueItem(props.baseUrl, sessionId, itemId, props.apiKey)
    await refreshQueue(sessionId)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function postToParent(payload: Record<string, unknown>) {
  if (typeof window === 'undefined' || !window.parent || window.parent === window) return
  window.parent.postMessage({ source: 'agent-factory', ...payload }, '*')
}

function sessionTitleFromMessage(message: string): string {
  const text = message.replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > 40 ? `${text.slice(0, 40)}…` : text
}

function notifySession(sessionId: string, title?: string) {
  activeSessionId.value = sessionId
  emit('session', sessionId)
  postToParent({
    type: 'session',
    sessionId,
    agentId: props.agentId,
    ...(title ? { title } : {}),
  })
}

function requestHostCreateSession(message?: string) {
  if (awaitingHostSession.value) return
  awaitingHostSession.value = true
  const payload = {
    type: 'session_create_request',
    agentId: props.agentId,
    userKey: props.userKey || null,
    locale: props.locale || 'zh',
    promptVars: props.promptVars || {},
    clientDeviceId: props.clientDeviceId || null,
    message: message?.trim() || null,
    title: message ? sessionTitleFromMessage(message) : null,
  }
  emit('session-create-request', payload)
  postToParent(payload)
}

async function applyHostSession(sessionId: string) {
  const id = String(sessionId || '').trim()
  if (!id) return
  awaitingHostSession.value = false
  const prev = activeSessionId.value
  activeSessionId.value = id
  emit('session', id)
  if (prev !== id) {
    stopPolling()
    abortRef.value?.abort()
    abortRef.value = null
    await loadHistory(id)
  }
  const pending = pendingSendText.value.trim()
  if (pending) {
    pendingSendText.value = ''
    await send(pending)
  }
}

function focusComposer() {
  // Wait for bootstrapping→disabled=false to flush before focusing contenteditable.
  void nextTick(() => {
    void nextTick(() => {
      try {
        window.focus()
      } catch {
        // ignore
      }
      void composerRef.value?.focus()
    })
  })
}

async function applySuggestedPrompt(prompt: string) {
  const text = prompt.trim()
  if (!text || !canSend.value) return
  await composerRef.value?.setText(text)
}

function clearHostSession() {
  awaitingHostSession.value = false
  pendingSendText.value = ''
  activeSessionId.value = undefined
  turns.value = []
  queueItems.value = []
  boardArtifacts.value = []
  boardError.value = ''
  boardOpen.value = false
  boardFullscreen.value = false
  loading.value = false
  activeRunId.value = null
  stopPolling()
  focusComposer()
}

function clampBoardWidth(px: number, containerWidth: number) {
  const maxByRatio = Math.floor(containerWidth * BOARD_WIDTH_MAX_RATIO)
  const maxByChat = Math.max(BOARD_WIDTH_MIN, containerWidth - CHAT_WIDTH_MIN)
  const max = Math.max(BOARD_WIDTH_MIN, Math.min(maxByRatio, maxByChat))
  return Math.min(max, Math.max(BOARD_WIDTH_MIN, Math.round(px)))
}

function stopBoardResize() {
  boardResizeCleanup?.()
  boardResizeCleanup = null
  boardResizing.value = false
}

function onBoardResizeStart(event: PointerEvent) {
  if (boardFullscreen.value) return
  event.preventDefault()
  const root = chatRootRef.value
  if (!root) return
  const startX = event.clientX
  const startWidth = boardWidthPx.value
  const containerWidth = root.getBoundingClientRect().width
  boardResizing.value = true

  const onMove = (ev: PointerEvent) => {
    // Dragging the handle left → wider board.
    const next = startWidth + (startX - ev.clientX)
    boardWidthPx.value = clampBoardWidth(next, containerWidth)
  }
  const onUp = () => stopBoardResize()
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  window.addEventListener('pointercancel', onUp)
  boardResizeCleanup = () => {
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onUp)
  }
}

function toggleBoardFullscreen() {
  boardFullscreen.value = !boardFullscreen.value
}

function onBoardKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && boardFullscreen.value) {
    boardFullscreen.value = false
  }
}

function notifyBoardState() {
  postToParent({
    type: 'board_state',
    open: boardOpen.value,
    count: boardArtifacts.value.length,
  })
}

async function setBoardOpen(open: boolean) {
  boardOpen.value = open
  if (!open) {
    boardFullscreen.value = false
    stopBoardResize()
  } else {
    await refreshBoard()
  }
  notifyBoardState()
}

function onHostMessage(event: MessageEvent) {
  const data = event.data
  if (!data || data.source !== 'agent-factory-host') return
  if (data.type === 'set_session' && data.sessionId) {
    void applyHostSession(String(data.sessionId))
  } else if (data.type === 'clear_session') {
    clearHostSession()
    notifyBoardState()
  } else if (data.type === 'toggle_board') {
    void setBoardOpen(!boardOpen.value)
  } else if (data.type === 'set_board_open') {
    void setBoardOpen(!!data.open)
  } else if (data.type === 'session_create_failed') {
    awaitingHostSession.value = false
    if (pendingSendText.value) {
      input.value = pendingSendText.value
      pendingSendText.value = ''
    }
    errorText.value = String(data.error || '业务侧创建会话失败')
    emit('error', errorText.value)
  }
}

function estimateTurnSize(turn?: ChatTurn): number {
  if (!turn) return 240
  let h = 170
  h += Math.min(140, Math.ceil((turn.userMessage?.length || 0) / 36) * 22)
  const hasProcess = !!(turn.reasoning?.trim() || turn.tools.length)
  if (hasProcess) {
    h += 26
    if (turn.processExpanded) {
      const stepCount = (turn.reasoning?.trim() ? 1 : 0) + turn.tools.length
      h += 10 + stepCount * 28
      // 详情完全撑开：按内容粗估，交给列表 measure 纠正
      if (turn.reasoning?.trim()) {
        h += Math.min(480, 40 + Math.ceil(turn.reasoning.length / 6))
      }
      for (const tool of turn.tools) {
        if (tool.status === 'running' || tool.result !== undefined || tool.arguments !== undefined) {
          h += 80
        }
      }
    }
  }
  // HITL cards are docked above the composer, not inside the virtualized turn.
  for (const interim of turn.interimMessages || []) {
    h += Math.min(240, Math.max(28, Math.ceil(interim.length / 42) * 22))
  }
  h += Math.min(640, Math.max(40, Math.ceil((turn.assistantContent?.length || 0) / 42) * 24))
  if (turn.status === 'running' && !turn.assistantContent) h += 44
  if (turn.error) h += 24
  return Math.max(200, h)
}

const rowVirtualizer = useVirtualizer(
  computed(() => ({
    count: turns.value.length,
    getScrollElement: () => listRef.value,
    estimateSize: (index: number) => estimateTurnSize(turns.value[index]),
    overscan: 6,
    gap: 20,
    getItemKey: (index: number) => turns.value[index]?.id ?? index,
  })),
)

const virtualTurns = computed(() => rowVirtualizer.value.getVirtualItems())
const listTotalSize = computed(() => rowVirtualizer.value.getTotalSize())

function measureTurnEl(el: unknown) {
  if (el instanceof HTMLElement) {
    rowVirtualizer.value.measureElement(el)
  }
}

function distanceFromBottom(el: HTMLElement): number {
  return el.scrollHeight - el.scrollTop - el.clientHeight
}

function markProgrammaticScroll() {
  programmaticScroll = true
  if (programmaticScrollTimer != null) clearTimeout(programmaticScrollTimer)
  programmaticScrollTimer = setTimeout(() => {
    programmaticScroll = false
    programmaticScrollTimer = null
  }, 120)
}

function releaseStickToBottom() {
  if (!stickToBottom.value) return
  stickToBottom.value = false
  if (scrollRaf != null) {
    cancelAnimationFrame(scrollRaf)
    scrollRaf = null
  }
}

function onListScroll() {
  if (programmaticScroll) return
  const el = listRef.value
  if (!el) return
  const distance = distanceFromBottom(el)
  // Hysteresis: avoid threshold flicker that fights the user mid-scroll.
  if (stickToBottom.value) {
    if (distance > EXIT_STICK_PX) stickToBottom.value = false
  } else if (distance <= ENTER_STICK_PX) {
    stickToBottom.value = true
  }
}

/** Wheel / touch up = user intent to leave the bottom; unlock immediately. */
function onListWheel(event: WheelEvent) {
  if (event.deltaY < 0) releaseStickToBottom()
}

function onListTouchStart(event: TouchEvent) {
  lastTouchY = event.touches[0]?.clientY ?? null
}

function onListTouchMove(event: TouchEvent) {
  const y = event.touches[0]?.clientY
  if (y == null || lastTouchY == null) return
  // Finger moving down → content moves up (reading history).
  if (y - lastTouchY > 2) releaseStickToBottom()
  lastTouchY = y
}

function onListTouchEnd() {
  lastTouchY = null
}

async function scrollToBottom(force = false) {
  if (!force && !stickToBottom.value) return
  if (scrollRaf != null) {
    cancelAnimationFrame(scrollRaf)
    scrollRaf = null
  }
  await nextTick()
  if (!force && !stickToBottom.value) return
  if (!turns.value.length) return

  const run = () => {
    scrollRaf = null
    if (!force && !stickToBottom.value) return
    markProgrammaticScroll()
    rowVirtualizer.value.scrollToIndex(turns.value.length - 1, {
      align: 'end',
      behavior: 'auto',
    })
    // After first paint / measure, pin hard to bottom (streaming deltas).
    requestAnimationFrame(() => {
      const el = listRef.value
      if (!el || (!force && !stickToBottom.value)) return
      markProgrammaticScroll()
      el.scrollTop = el.scrollHeight
    })
  }
  scrollRaf = requestAnimationFrame(run)
}

function jumpToBottom() {
  stickToBottom.value = true
  void scrollToBottom(true)
}

function stopPolling() {
  if (pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

/** Fallback when event hub is gone: coarse timeline polling. */
function startTimelinePoll() {
  const running = turns.value.find((t) => t.status === 'running')
  const waiting = turns.value.find(
    (t) => t.status === 'waiting_human' && (!!t.humanAsk || !!t.humanApproval),
  )
  if (running) {
    loading.value = true
    activeRunId.value = running.runId || running.id
  } else if (waiting) {
    loading.value = false
    activeRunId.value = waiting.runId || waiting.id
    // No need to poll while parked on HITL — Stop/Approve will refresh.
    stopPolling()
    return
  } else if (!queueItems.value.length) {
    loading.value = false
    if (!abortRef.value) activeRunId.value = null
    stopPolling()
    return
  } else {
    // Waiting for server-side dispatch of queued messages.
    loading.value = false
    if (!abortRef.value) activeRunId.value = null
  }
  if (pollTimer != null) return
  pollTimer = setInterval(() => {
    void refreshRunningProgress()
  }, 1200)
}

async function refreshRunningProgress() {
  const sessionId = activeSessionId.value
  if (!sessionId) return
  try {
    const timeline = await fetchTimeline(props.baseUrl, sessionId, props.apiKey)
    const nextTurns = timelineToTurns(timeline.runs || [])
    // Preserve in-flight assistant buffer while merging timeline interims.
    for (const next of nextTurns) {
      if (next.status !== 'running' && next.status !== 'waiting_human') continue
      const prev = turns.value.find((t) => t.runId === next.runId || t.id === next.id)
      if (!prev) continue
      const live = (prev.assistantContent || '').trim()
      const seeded = (next.assistantContent || '').trim()
      if (live && live !== seeded && !(next.interimMessages || []).includes(live)) {
        next.assistantContent = prev.assistantContent
      }
      // Prefer the richer interim list (timeline should win; keep extras from live).
      const fromTimeline = next.interimMessages || []
      const fromLive = prev.interimMessages || []
      if (fromLive.length > fromTimeline.length) {
        const merged: string[] = [...fromTimeline]
        for (const msg of fromLive) {
          if (!merged.includes(msg)) merged.push(msg)
        }
        next.interimMessages = merged
      }
    }
    turns.value = nextTurns
    await refreshQueue(sessionId)
    const running = turns.value.find((t) => t.status === 'running')
    const waiting = turns.value.find((t) => t.status === 'waiting_human')
    if (running) {
      loading.value = true
      activeRunId.value = running.runId || running.id
      startTimelinePoll()
    } else if (waiting) {
      loading.value = false
      activeRunId.value = waiting.runId || waiting.id
      stopPolling()
    } else if (queueItems.value.length) {
      // Waiting for server to dispatch next queued item.
      loading.value = false
      activeRunId.value = null
      startTimelinePoll()
    } else {
      loading.value = false
      activeRunId.value = null
      stopPolling()
    }
    await scrollToBottom(false)
  } catch {
    // keep previous UI; next poll retries
  }
}

function applyRunEvent(
  getTurn: () => ChatTurn | undefined,
  event: Record<string, unknown>,
  opts?: { onTerminal?: () => void },
): void {
  const turn = getTurn()
  if (!turn) return
  const type = event.type
  if (type === 'session' && typeof event.session_id === 'string') {
    notifySession(event.session_id)
  } else if (type === 'run_start' && typeof event.run_id === 'string') {
    turn.runId = event.run_id
    turn.id = event.run_id
    activeRunId.value = event.run_id
  } else if (type === 'reasoning_delta') {
    turn.reasoning += String(event.delta || '')
    turn.processExpanded = true
  } else if (type === 'reasoning_done') {
    if (!turn.reasoning && event.content) {
      turn.reasoning = String(event.content)
    }
  } else if (type === 'message_delta') {
    turn.assistantContent += String(event.delta || '')
  } else if (type === 'message' || type === 'message_done') {
    // Final (or complete) assistant text for this round — replace, don't append.
    if (typeof event.content === 'string') {
      turn.assistantContent = event.content
    }
  } else if (type === 'tool_call') {
    const callId = String(event.call_id || Date.now())
    const existing = turn.tools.find((t) => t.callId === callId)
    if (!existing) {
      // First tool of a model round: archive streamed progress as one interim batch.
      const batch = turn.assistantContent.trim()
      if (batch) {
        turn.interimMessages = [...(turn.interimMessages || []), batch]
        turn.assistantContent = ''
      }
      turn.tools.push({
        callId,
        name: String(event.name || 'tool'),
        arguments: event.arguments,
        status: 'running',
      })
    }
    turn.processExpanded = true
  } else if (type === 'tool_result') {
    const callId = String(event.call_id || '')
    const target =
      turn.tools.find((t) => t.callId === callId) || turn.tools[turn.tools.length - 1]
    if (target) {
      target.result = event.result
      target.status = 'done'
    }
  } else if (type === 'context_compacted') {
    const layers = Array.isArray(event.layers) ? event.layers.map(String) : []
    const before = event.estimated_tokens_before
    const after = event.estimated_tokens_after
    contextHint.value = `上下文已压缩（${layers.join('+') || 'L1'}）估点 ${before} → ${after}`
  } else if (type === 'queue_enqueued') {
    const queue = Array.isArray(event.queue) ? (event.queue as QueueItemDto[]) : null
    if (queue) {
      queueItems.value = queue.filter((q) => q.status === 'queued')
    } else {
      void refreshQueue()
    }
  } else if (type === 'human_input_required') {
    const options = Array.isArray(event.options)
      ? event.options.map((x) => String(x).trim()).filter(Boolean)
      : []
    const allowFree =
      event.allow_free_text === undefined ? true : Boolean(event.allow_free_text)
    turn.status = 'waiting_human'
    turn.humanApproval = undefined
    turn.humanAsk = {
      callId: String(event.call_id || ''),
      question: String(event.question || '请提供更多信息以继续。'),
      options,
      allowFreeText: allowFree || options.length === 0,
      expiresAt: event.expires_at ? String(event.expires_at) : null,
      draft: turn.humanAsk?.draft || '',
    }
    turn.processExpanded = true
    loading.value = false
    // Keep run id so Stop can cancel waiting_human runs.
    if (typeof event.run_id === 'string' && event.run_id) {
      activeRunId.value = event.run_id
      turn.runId = event.run_id
    } else if (turn.runId) {
      activeRunId.value = turn.runId
    }
  } else if (type === 'tool_approval_required') {
    const args = event.arguments
    const reasonRaw = event.reason
    const toolDescRaw = event.tool_description
    turn.status = 'waiting_human'
    turn.humanAsk = undefined
    turn.humanApproval = {
      callId: String(event.call_id || ''),
      name: String(event.name || 'tool'),
      arguments: args ?? {},
      reason:
        typeof reasonRaw === 'string' && reasonRaw.trim() ? reasonRaw.trim() : null,
      toolDescription:
        typeof toolDescRaw === 'string' && toolDescRaw.trim()
          ? toolDescRaw.trim()
          : null,
      expiresAt: event.expires_at ? String(event.expires_at) : null,
      editMode: turn.humanApproval?.editMode || false,
      editedArgumentsJson:
        turn.humanApproval?.editedArgumentsJson ||
        JSON.stringify(args ?? {}, null, 2),
    }
    turn.processExpanded = true
    loading.value = false
    // Keep run id so Stop can cancel waiting_human runs.
    if (typeof event.run_id === 'string' && event.run_id) {
      activeRunId.value = event.run_id
      turn.runId = event.run_id
    } else if (turn.runId) {
      activeRunId.value = turn.runId
    }
  } else if (type === 'cancelled') {
    turn.status = 'cancelled'
    turn.humanAsk = undefined
    turn.humanApproval = undefined
    turn.processExpanded = false
    opts?.onTerminal?.()
  } else if (type === 'error') {
    if (event.code === 'hub_unavailable') {
      // Resume fell back — keep running and poll timeline.
      return
    }
    turn.error = String(event.error || '未知错误')
    turn.status = event.code === 'limit_reached' ? 'limit_reached' : 'failed'
    turn.humanAsk = undefined
    turn.humanApproval = undefined
    errorText.value = turn.error
    emit('error', errorText.value)
    ElMessage.error(errorText.value)
    opts?.onTerminal?.()
  } else if (type === 'done') {
    const st = String(event.status || 'completed')
    if (st === 'queued') {
      // Enqueue ack — not a turn terminal.
      opts?.onTerminal?.()
      void refreshQueue()
      return
    }
    if (st === 'waiting_human') {
      turn.status = 'waiting_human'
      turn.processExpanded = true
      loading.value = false
      activeRunId.value = null
      opts?.onTerminal?.()
      return
    }
    if (st === 'cancelled' || st === 'failed' || st === 'limit_reached' || st === 'completed') {
      turn.status = st
    } else {
      turn.status = 'completed'
    }
    turn.humanAsk = undefined
    turn.humanApproval = undefined
    turn.processExpanded = false
    activeRunId.value = null
    void refreshQueue()
    opts?.onTerminal?.()
  }
}
async function resumeRunningTurn(turn: ChatTurn) {
  const runId = turn.runId || turn.id
  const wasWaiting = turn.status === 'waiting_human'
  if (!wasWaiting) {
    loading.value = true
    activeRunId.value = runId
  }
  stopPolling()
  userStopped.value = false

  // Re-hydrate completed rounds from timeline (source of truth for interim batches).
  // Do NOT wipe interimMessages — full hub replay is huge on long tool runs and
  // often drops history, leaving only the latest assistant text.
  const sessionId = activeSessionId.value
  if (!wasWaiting && sessionId) {
    try {
      const timeline = await fetchTimeline(props.baseUrl, sessionId, props.apiKey)
      const seeded = timelineToTurns(timeline.runs || []).find(
        (t) => t.runId === runId || t.id === runId,
      )
      if (seeded) {
        turn.reasoning = seeded.reasoning
        turn.tools = seeded.tools
        turn.interimMessages = [...(seeded.interimMessages || [])]
        // Keep any newer live buffer if timeline has not caught the in-flight round.
        if (!(turn.assistantContent || '').trim()) {
          turn.assistantContent = seeded.assistantContent
        }
        turn.humanAsk = seeded.humanAsk
        turn.humanApproval = seeded.humanApproval
        turn.status = seeded.status === 'waiting_human' ? 'waiting_human' : 'running'
      } else {
        turn.status = 'running'
      }
    } catch {
      turn.status = 'running'
    }
  }
  turn.processExpanded = true
  turn.error = undefined

  const turnId = turn.id
  const getTurn = () => turns.value.find((t) => t.id === turnId || t.runId === runId)
  const ac = new AbortController()
  abortRef.value = ac
  let sawTerminal = false
  let deltaScrollTicks = 0
  let hubUnavailable = false
  // Skip buffered replay (after >> hub length); timeline already seeded history.
  // Live deltas for the in-flight round still arrive.
  const liveOnlyAfter = Number.MAX_SAFE_INTEGER

  try {
    await streamRunEvents(
      props.baseUrl,
      runId,
      props.apiKey,
      {
        onEvent: (event) => {
          if (event.type === 'error' && event.code === 'hub_unavailable') {
            hubUnavailable = true
            return
          }
          applyRunEvent(getTurn, event, {
            onTerminal: () => {
              sawTerminal = true
            },
          })
          const type = String(event.type || '')
          deltaScrollTicks += 1
          if (
            type === 'message_delta' ||
            type === 'reasoning_delta' ||
            deltaScrollTicks % 3 === 0 ||
            type === 'done' ||
            type === 'tool_call' ||
            type === 'tool_result' ||
            type === 'human_input_required'
          ) {
            void scrollToBottom(false)
          }
        },
      },
      ac.signal,
      liveOnlyAfter,
    )
    if (hubUnavailable || (getTurn()?.status === 'running' && !sawTerminal)) {
      await refreshRunningProgress()
      startTimelinePoll()
    }
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') {
      if (userStopped.value) {
        const t = getTurn()
        if (t) {
          t.status = 'cancelled'
          t.processExpanded = false
        }
        loading.value = false
      }
      // else: navigating away; server keeps running
      return
    }
    if (!wasWaiting) startTimelinePoll()
  } finally {
    if (abortRef.value === ac) abortRef.value = null
    if (!hasRunningTurn.value) {
      loading.value = false
      if (getTurn()?.status !== 'waiting_human') {
        activeRunId.value = null
      }
      stopPolling()
    }
    await scrollToBottom(false)
  }
}

async function refreshBoard(sessionId?: string) {
  const id = (sessionId || activeSessionId.value || '').trim()
  if (!id) {
    boardArtifacts.value = []
    boardError.value = ''
    return
  }
  boardLoading.value = true
  boardError.value = ''
    try {
    const board = await fetchSessionBoard(props.baseUrl, id, props.apiKey)
    boardArtifacts.value = board.artifacts || []
    notifyBoardState()
  } catch (err) {
    boardError.value = err instanceof Error ? err.message : String(err)
  } finally {
    boardLoading.value = false
  }
}

async function toggleBoard() {
  await setBoardOpen(!boardOpen.value)
}

async function loadHistory(sessionId: string) {
  const [timeline] = await Promise.all([
    fetchTimeline(props.baseUrl, sessionId, props.apiKey),
    refreshQueue(sessionId),
    refreshBoard(sessionId),
  ])
  turns.value = timelineToTurns(timeline.runs || [])
  const running = turns.value.find((t) => t.status === 'running')
  const waiting = turns.value.find((t) => t.status === 'waiting_human')
  if (running) {
    void resumeRunningTurn(running)
  } else if (waiting?.humanAsk || waiting?.humanApproval) {
    // Rehydrate HITL card via events endpoint (no live hub).
    void resumeRunningTurn(waiting)
  } else {
    loading.value = false
    activeRunId.value = null
    stopPolling()
    // Idle session may still have queued items (e.g. after refresh mid-queue).
    // Server lifespan also resumes; poll timeline briefly in case a run starts.
    if (queueItems.value.length) {
      startTimelinePoll()
    }
  }
}

async function submitHumanAnswer(turn: ChatTurn, selectedOption?: string) {
  const ask = turn.humanAsk
  const runId = turn.runId || turn.id
  if (!ask || !runId || ask.submitting) return
  const answer = (selectedOption ?? ask.draft ?? '').trim()
  if (!answer && !selectedOption) {
    ElMessage.warning('请先填写或选择回答')
    return
  }
  ask.submitting = true
  loading.value = true
  errorText.value = ''
  userStopped.value = false
  stopPolling()
  turn.status = 'running'
  turn.processExpanded = true
  activeRunId.value = runId

  const turnId = turn.id
  const getTurn = () => turns.value.find((t) => t.id === turnId || t.runId === runId)
  const ac = new AbortController()
  abortRef.value = ac
  let sawTerminal = false

  try {
    await streamResumeRun(
      props.baseUrl,
      runId,
      {
        call_id: ask.callId,
        answer: selectedOption ? undefined : answer,
        selected_option: selectedOption || null,
      },
      props.apiKey,
      {
        onEvent: (event) => {
          applyRunEvent(getTurn, event, {
            onTerminal: () => {
              sawTerminal = true
            },
          })
          const type = String(event.type || '')
          if (
            type === 'message_delta' ||
            type === 'reasoning_delta' ||
            type === 'done' ||
            type === 'tool_call' ||
            type === 'tool_result' ||
            type === 'human_input_required'
          ) {
            void scrollToBottom(false)
          }
        },
      },
      ac.signal,
    )
    const t = getTurn()
    if (t && t.status === 'running' && !sawTerminal && t.runId) {
      void resumeRunningTurn(t)
    }
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') {
      return
    }
    errorText.value = err instanceof Error ? err.message : String(err)
    const t = getTurn()
    if (t) {
      t.status = 'waiting_human'
      if (t.humanAsk) t.humanAsk.submitting = false
      t.error = errorText.value
    }
    emit('error', errorText.value)
    ElMessage.error(errorText.value)
  } finally {
    if (abortRef.value === ac) abortRef.value = null
    const t = getTurn()
    if (t?.humanAsk) t.humanAsk.submitting = false
    if (!hasRunningTurn.value) {
      loading.value = false
      if (t?.status !== 'waiting_human') activeRunId.value = null
    }
    await scrollToBottom(false)
  }
}

async function submitHumanApproval(
  turn: ChatTurn,
  decision: 'approve' | 'edit' | 'reject',
  opts?: { grantRun?: boolean },
) {
  const approval = turn.humanApproval
  const runId = turn.runId || turn.id
  if (!approval || !runId || approval.submitting) return

  let editedArguments: Record<string, unknown> | undefined
  if (decision === 'edit') {
    try {
      const parsed = JSON.parse(approval.editedArgumentsJson || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        ElMessage.warning('修改后的参数必须是 JSON 对象')
        return
      }
      editedArguments = parsed as Record<string, unknown>
    } catch {
      ElMessage.warning('参数 JSON 无法解析')
      return
    }
  }

  approval.submitting = true
  loading.value = true
  errorText.value = ''
  userStopped.value = false
  stopPolling()
  turn.status = 'running'
  turn.processExpanded = true
  activeRunId.value = runId

  const turnId = turn.id
  const getTurn = () => turns.value.find((t) => t.id === turnId || t.runId === runId)
  const ac = new AbortController()
  abortRef.value = ac
  let sawTerminal = false
  const grantRun = Boolean(opts?.grantRun) && decision !== 'reject'

  try {
    await streamResumeRun(
      props.baseUrl,
      runId,
      {
        call_id: approval.callId,
        decision,
        edited_arguments: decision === 'edit' ? editedArguments : undefined,
        grant_run: grantRun || undefined,
      },
      props.apiKey,
      {
        onEvent: (event) => {
          applyRunEvent(getTurn, event, {
            onTerminal: () => {
              sawTerminal = true
            },
          })
          const type = String(event.type || '')
          if (
            type === 'message_delta' ||
            type === 'reasoning_delta' ||
            type === 'done' ||
            type === 'tool_call' ||
            type === 'tool_result' ||
            type === 'human_input_required' ||
            type === 'tool_approval_required'
          ) {
            void scrollToBottom(false)
          }
        },
      },
      ac.signal,
    )
    const t = getTurn()
    if (t && t.status === 'running' && !sawTerminal && t.runId) {
      void resumeRunningTurn(t)
    }
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') {
      return
    }
    errorText.value = err instanceof Error ? err.message : String(err)
    const t = getTurn()
    if (t) {
      t.status = 'waiting_human'
      if (t.humanApproval) t.humanApproval.submitting = false
      t.error = errorText.value
    }
    emit('error', errorText.value)
    ElMessage.error(errorText.value)
  } finally {
    if (abortRef.value === ac) abortRef.value = null
    const t = getTurn()
    if (t?.humanApproval) t.humanApproval.submitting = false
    if (!hasRunningTurn.value) {
      loading.value = false
      if (t?.status !== 'waiting_human') activeRunId.value = null
    }
    await scrollToBottom(false)
  }
}

async function ensureSession(): Promise<string> {
  if (activeSessionId.value) return activeSessionId.value
  if (props.sessionCreateMode === 'host') {
    requestHostCreateSession()
    throw new Error('HOST_SESSION_PENDING')
  }
  const userKey = (props.userKey || '').trim()
  if (!userKey) {
    throw new Error('缺少 userKey：新建会话需要业务用户标识')
  }
  const created = await createSession(
    props.baseUrl,
    props.agentId,
    props.apiKey,
    userKey,
    props.promptVars,
    props.locale,
    props.clientDeviceId,
  )
  notifySession(created.id)
  return created.id
}

async function bootstrap() {
  bootstrapping.value = true
  errorText.value = ''
  try {
    if (!props.agentId) {
      throw new Error('缺少 agentId')
    }
    const [agent, modelList] = await Promise.all([
      fetchAgent(props.baseUrl, props.agentId, props.apiKey),
      fetchModels(props.baseUrl, props.apiKey),
    ])
    agentName.value = (agent.name || '').trim()
    agentLogoUrl.value = resolveMediaUrl((agent.logo_url || '').trim(), props.baseUrl)
    welcomeTitle.value = (agent.welcome_title || '').trim()
    welcomeSubtitle.value = (agent.welcome_subtitle || '').trim()
    suggestedPrompts.value = Array.isArray(agent.suggested_prompts)
      ? agent.suggested_prompts.map((item) => String(item || '').trim()).filter(Boolean)
      : []
    uiTheme.value =
      agent.ui_theme && typeof agent.ui_theme === 'object' ? { ...agent.ui_theme } : {}
    models.value = modelList
    skills.value = (agent.skills || []).filter((s) => (s.name || '').trim())
    selectedModelProfileId.value = pickDefaultModelId(modelList, agent.model_profile_id)

    if (props.sessionId) {
      activeSessionId.value = props.sessionId
      await loadHistory(props.sessionId)
      notifySession(props.sessionId)
    } else if (activeSessionId.value) {
      await loadHistory(activeSessionId.value)
    } else {
      turns.value = []
    }
  } catch (err) {
    skills.value = []
    errorText.value = err instanceof Error ? err.message : String(err)
    emit('error', errorText.value)
    ElMessage.error(errorText.value)
  } finally {
    bootstrapping.value = false
    stickToBottom.value = true
    await scrollToBottom(true)
    postToParent({ type: 'ready', agentId: props.agentId })
    if (!turns.value.length) focusComposer()
  }
}

function toggleProcess(turn: ChatTurn) {
  turn.processExpanded = !turn.processExpanded
  // Height changes; ResizeObserver on the row will remeasure. Keep stick if pinned.
  void nextTick(() => {
    void scrollToBottom(false)
  })
}

function resolveStopRunId(): string | null {
  if (activeRunId.value) return activeRunId.value
  const waiting = turns.value.find(
    (t) => t.status === 'waiting_human' && (!!t.humanAsk || !!t.humanApproval),
  )
  if (waiting?.runId) return waiting.runId
  const running = turns.value.find((t) => t.status === 'running')
  return running?.runId || running?.id || null
}

async function stop() {
  userStopped.value = true
  const runId = resolveStopRunId()
  if (runId) {
    try {
      await cancelRun(props.baseUrl, runId, props.apiKey)
    } catch {
      // ignore
    }
  }
  // Clear HITL cards locally even if server cancel races.
  for (const t of turns.value) {
    if (t.status === 'waiting_human') {
      t.status = 'cancelled'
      t.humanAsk = undefined
      t.humanApproval = undefined
    }
  }
  abortRef.value?.abort()
  // Cancel is cooperative; poll until timeline shows terminal status.
  startTimelinePoll()
}

async function send(rawText?: string) {
  if (!canSend.value || hasWaitingHuman.value) return
  const text = (rawText ?? input.value).trim()
  if (!text) return
  input.value = ''
  errorText.value = ''
  userStopped.value = false
  stickToBottom.value = true

  // Host-owned session: ask business to create, keep draft until set_session.
  if (!activeSessionId.value && props.sessionCreateMode === 'host') {
    pendingSendText.value = text
    requestHostCreateSession(text)
    ElMessage.info('已请求业务侧创建会话')
    return
  }

  const sessionBusy = hasRunningTurn.value
  // Queue while a run is in flight — no optimistic turn.
  if (sessionBusy) {
    enqueueing.value = true
    try {
      const sessionId = await ensureSession()
      let enqueued = false
      await streamChat(
        props.baseUrl,
        {
          agent_id: props.agentId,
          message: text,
          session_id: sessionId,
          user_key: props.userKey || null,
          locale: props.locale || 'zh',
          prompt_vars: props.promptVars || {},
          model_profile_id: selectedModelProfileId.value || null,
        },
        props.apiKey,
        {
          onEvent: (event) => {
            if (event.type === 'session' && typeof event.session_id === 'string') {
              notifySession(event.session_id)
            } else if (event.type === 'queue_enqueued') {
              enqueued = true
              const queue = Array.isArray(event.queue)
                ? (event.queue as QueueItemDto[])
                : null
              if (queue) {
                queueItems.value = queue.filter((q) => q.status === 'queued')
              } else {
                void refreshQueue(sessionId)
              }
            }
          },
        },
      )
      if (enqueued) {
        ElMessage.success('已加入排队，当前回复结束后自动执行')
      } else {
        // Race: became idle and started immediately — refresh UI.
        await refreshRunningProgress()
      }
    } catch (err) {
      if (err instanceof Error && err.message === 'HOST_SESSION_PENDING') {
        pendingSendText.value = text
        return
      }
      errorText.value = err instanceof Error ? err.message : String(err)
      emit('error', errorText.value)
      ElMessage.error(errorText.value)
    } finally {
      enqueueing.value = false
      await scrollToBottom(false)
    }
    return
  }

  loading.value = true
  stopPolling()

  let sessionId: string
  try {
    sessionId = await ensureSession()
  } catch (err) {
    loading.value = false
    if (err instanceof Error && err.message === 'HOST_SESSION_PENDING') {
      pendingSendText.value = text
      return
    }
    errorText.value = err instanceof Error ? err.message : String(err)
    emit('error', errorText.value)
    ElMessage.error(errorText.value)
    input.value = text
    return
  }

  const isFirstQuestion = turns.value.length === 0
  const localId = `local-${Date.now()}`
  turns.value.push({
    id: localId,
    status: 'running',
    userMessage: text,
    reasoning: '',
    tools: [],
    assistantContent: '',
    interimMessages: [],
    processExpanded: true,
  })
  // First user question becomes the session title for the host sidebar.
  if (isFirstQuestion) {
    notifySession(sessionId, sessionTitleFromMessage(text))
  }
  // Must mutate the reactive proxy in the array; mutating the raw object
  // before/aside from the proxy will NOT trigger Vue re-renders (looks like
  // "all content appears at once" when loading flips to false).
  const turnIdx = turns.value.length - 1
  const currentTurn = () => turns.value[turnIdx]
  await scrollToBottom(true)

  const ac = new AbortController()
  abortRef.value = ac
  let deltaScrollTicks = 0
  let sawTerminal = false

  try {
    await streamChat(
      props.baseUrl,
      {
        agent_id: props.agentId,
        message: text,
        session_id: sessionId,
        user_key: props.userKey || null,
        locale: props.locale || 'zh',
        prompt_vars: props.promptVars || {},
        model_profile_id: selectedModelProfileId.value || null,
      },
      props.apiKey,
      {
        onEvent: (event) => {
          applyRunEvent(currentTurn, event, {
            onTerminal: () => {
              sawTerminal = true
            },
          })
          const type = String(event.type || '')
          deltaScrollTicks += 1
          if (
            type === 'message_delta' ||
            type === 'reasoning_delta' ||
            deltaScrollTicks % 3 === 0 ||
            type === 'done' ||
            type === 'tool_call' ||
            type === 'tool_result' ||
            type === 'human_input_required'
          ) {
            void scrollToBottom(false)
          }
        },
      },
      ac.signal,
    )
    const turn = currentTurn()
    if (turn && turn.status === 'running' && !sawTerminal && turn.runId) {
      // SSE ended but server keeps going — reconnect to the same run hub.
      void resumeRunningTurn(turn)
    } else if (queueItems.value.length && !hasRunningTurn.value) {
      startTimelinePoll()
    }
  } catch (err) {
    const turn = currentTurn()
    if ((err as Error)?.name === 'AbortError') {
      if (userStopped.value && turn) {
        turn.status = 'cancelled'
        turn.processExpanded = false
        loading.value = false
      } else if (turn?.runId && turn.status === 'running') {
        // Navigation/refresh aborted the reader; another mount will resume.
      }
    } else {
      errorText.value = err instanceof Error ? err.message : String(err)
      if (turn) {
        turn.status = 'failed'
        turn.error = errorText.value
      }
      loading.value = false
      emit('error', errorText.value)
      ElMessage.error(errorText.value)
    }
  } finally {
    if (abortRef.value === ac) abortRef.value = null
    if (!hasRunningTurn.value) {
      loading.value = false
      activeRunId.value = null
    }
    await scrollToBottom(false)
  }
}

onMounted(() => {
  window.addEventListener('message', onHostMessage)
  window.addEventListener('keydown', onBoardKeydown)
  void bootstrap()
})
onBeforeUnmount(() => {
  window.removeEventListener('message', onHostMessage)
  window.removeEventListener('keydown', onBoardKeydown)
  // Only drop the local SSE reader; server run keeps going.
  abortRef.value?.abort()
  stopPolling()
  stopBoardResize()
  if (scrollRaf != null) cancelAnimationFrame(scrollRaf)
  if (programmaticScrollTimer != null) clearTimeout(programmaticScrollTimer)
})
watch(
  () => [props.agentId, props.baseUrl],
  () => {
    stopPolling()
    void bootstrap()
  },
)
watch(hasRunningTurn, (running, wasRunning) => {
  // After a run finishes, Board may have new artifacts from board_write / delegates.
  if (wasRunning && !running && boardOpen.value) {
    void refreshBoard()
  }
})

watch(
  () => props.sessionId,
  (next) => {
    const id = next ? String(next) : ''
    if (!id) {
      if (props.sessionCreateMode === 'host') return
      if (activeSessionId.value) clearHostSession()
      return
    }
    if (id === activeSessionId.value) return
    void applyHostSession(id)
  },
)
</script>

<template>
  <div
    ref="chatRootRef"
    class="af-chat"
    :class="{
      'af-chat--no-header': !showHeader,
      'af-chat--board-open': boardOpen,
      'af-chat--board-fullscreen': boardOpen && boardFullscreen,
      'af-chat--board-resizing': boardResizing,
    }"
    :style="{
      ...themeStyle,
      '--af-board-width': `${boardWidthPx}px`,
    }"
    v-loading="bootstrapping"
  >
    <div v-show="!(boardOpen && boardFullscreen)" class="af-chat__main">
      <div v-if="showHeader" class="af-chat__header">
        <div class="af-chat__title">
          <div class="af-chat__avatar" aria-hidden="true">
            <el-icon><Service /></el-icon>
          </div>
          <div>
            <div class="af-chat__name">{{ displayName }}</div>
            <div class="af-chat__meta">
              <span class="af-chat__meta-item">{{ sessionLabel }}</span>
              <span class="af-chat__meta-dot" aria-hidden="true" />
              <span class="af-chat__meta-item" :title="agentId">
                {{ agentId.slice(0, 8) }}…
              </span>
            </div>
          </div>
        </div>
        <div class="af-chat__header-actions">
          <button
            type="button"
            class="af-chat__icon-btn"
            title="刷新"
            aria-label="刷新"
            @click="bootstrap"
          >
            <el-icon :size="18"><RefreshRight /></el-icon>
          </button>
          <button
            type="button"
            class="af-chat__icon-btn"
            :class="{ 'af-chat__icon-btn--active': boardOpen }"
            title="会话产物"
            aria-label="会话产物"
            @click="void toggleBoard()"
          >
            <el-icon :size="18"><Document /></el-icon>
            <span v-if="boardBadge" class="af-chat__badge">{{ boardBadge }}</span>
          </button>
        </div>
      </div>

      <el-alert
        v-if="errorText"
        class="af-chat__alert"
        :title="errorText"
        type="error"
        show-icon
        :closable="false"
      />
      <el-alert
        v-if="boardError && boardOpen"
        class="af-chat__alert"
        :title="boardError"
        type="warning"
        show-icon
        :closable="true"
        @close="boardError = ''"
      />
      <el-alert
        v-if="contextHint && !errorText"
        class="af-chat__alert"
        :title="contextHint"
        type="info"
        show-icon
        :closable="true"
        @close="contextHint = ''"
      />

      <div class="af-chat__list-wrap">
        <div
          ref="listRef"
          class="af-chat__list"
          @scroll.passive="onListScroll"
          @wheel.passive="onListWheel"
          @touchstart.passive="onListTouchStart"
          @touchmove.passive="onListTouchMove"
          @touchend.passive="onListTouchEnd"
        >
          <div v-if="!bootstrapping && !turns.length" class="af-empty">
            <div class="af-empty__mark" aria-hidden="true">
              <img
                v-if="agentLogoUrl"
                class="af-empty__logo"
                :src="agentLogoUrl"
                :alt="displayName"
              />
              <el-icon v-else><Service /></el-icon>
            </div>
            <h1 class="af-empty__title">{{ emptyTitle }}</h1>
            <p class="af-empty__desc">{{ emptyDesc }}</p>
            <div v-if="emptyPrompts.length" class="af-empty__tips">
              <button
                v-for="(prompt, idx) in emptyPrompts"
                :key="`${idx}-${prompt}`"
                type="button"
                class="af-empty__tip af-empty__tip--btn"
                @click="void applySuggestedPrompt(prompt)"
              >
                {{ prompt }}
              </button>
            </div>
            <div v-else class="af-empty__tips">
              <span class="af-empty__tip">清晰描述你的目标</span>
              <span class="af-empty__tip">可附带约束与上下文</span>
              <span v-if="skills.length" class="af-empty__tip">用 / 唤起技能</span>
            </div>
          </div>

          <div
            v-else
            class="af-chat__virt"
            :style="{ height: `${listTotalSize}px` }"
          >
            <div
              v-for="vr in virtualTurns"
              :key="String(vr.key)"
              class="af-turn-slot"
              :data-index="vr.index"
              :ref="measureTurnEl"
              :style="{
                transform: `translateY(${vr.start}px)`,
              }"
            >
              <ChatTurnItem
                v-if="turns[vr.index]"
                :turn="turns[vr.index]"
                hitl-placement="dock"
                @toggle-process="toggleProcess(turns[vr.index])"
              />
            </div>
          </div>
        </div>

        <button
          v-if="showJumpToBottom"
          type="button"
          class="af-chat__jump"
          @click="jumpToBottom"
        >
          <el-icon><Bottom /></el-icon>
          回到底部
        </button>
      </div>

      <div v-if="queueItems.length" class="af-queue">
        <span class="af-queue__label">排队 {{ queueItems.length }}</span>
        <div ref="queueStripRef" class="af-queue__strip" role="list">
          <div
            v-for="(item, idx) in queueItems"
            :key="item.id"
            class="af-queue__chip"
            role="listitem"
            :title="item.message"
          >
            <span class="af-queue__ord">{{ idx + 1 }}</span>
            <span class="af-queue__text">{{ queuePreview(item.message) }}</span>
            <button
              type="button"
              class="af-queue__x"
              :aria-label="`取消排队：${queuePreview(item.message, 12)}`"
              @click="void removeQueued(item.id)"
            >
              ×
            </button>
          </div>
        </div>
      </div>

      <div v-if="waitingHitlTurn" class="af-hitl-dock">
        <HitlCard
          :turn="waitingHitlTurn"
          @submit-answer="(opt) => void submitHumanAnswer(waitingHitlTurn, opt)"
          @submit-approval="(d, opts) => void submitHumanApproval(waitingHitlTurn, d, opts)"
        />
      </div>

      <ChatComposer
        ref="composerRef"
        v-model="input"
        v-model:model-profile-id="selectedModelProfileId"
        :models="models"
        :skills="skills"
        :disabled="bootstrapping || !agentId"
        :loading="loading || hasRunningTurn || hasWaitingHuman"
        :submit-disabled="hasWaitingHuman || enqueueing"
        :placeholder="composerPlaceholder"
        @submit="(text) => void send(text)"
        @stop="stop"
      />
    </div>

    <div
      v-if="boardOpen && !boardFullscreen"
      class="af-chat__board-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整产物面板宽度"
      @pointerdown="onBoardResizeStart"
    />

    <aside v-if="boardOpen" class="af-chat__board-pane" aria-label="会话产物">
      <BoardPanel
        :artifacts="boardArtifacts"
        :loading="boardLoading"
        :session-bound="!!activeSessionId"
        :fullscreen="boardFullscreen"
        @close="void setBoardOpen(false)"
        @refresh="void refreshBoard()"
        @toggle-fullscreen="toggleBoardFullscreen"
      />
    </aside>
  </div>
</template>
