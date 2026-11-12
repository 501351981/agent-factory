<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { ElMessage } from 'element-plus'
import { RefreshRight, Service, Bottom } from '@element-plus/icons-vue'
import {
  cancelQueueItem,
  cancelRun,
  createSession,
  fetchAgent,
  fetchModels,
  fetchQueue,
  fetchTimeline,
  streamChat,
  streamResumeRun,
  streamRunEvents,
  type ModelProfileDto,
  type QueueItemDto,
  type SkillDto,
} from './api'
import ChatComposer from './ChatComposer.vue'
import ChatTurnItem from './ChatTurnItem.vue'
import { timelineToTurns } from './timeline'
import type { ChatTurn } from './types'

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
const models = ref<ModelProfileDto[]>([])
const skills = ref<SkillDto[]>([])
const selectedModelProfileId = ref('')
/** Host mode: message waiting while parent creates a session. */
const pendingSendText = ref('')
const awaitingHostSession = ref(false)
/** When false, streaming won't yank the viewport (user scrolled up). */
const stickToBottom = ref(true)
const BOTTOM_THRESHOLD_PX = 72
let pollTimer: ReturnType<typeof setInterval> | null = null

const displayName = computed(() => agentName.value || 'Agent 助手')
const sessionLabel = computed(() => {
  if (!activeSessionId.value) return '新对话'
  return `会话 ${activeSessionId.value.slice(0, 8)}`
})
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
const canSend = computed(
  () =>
    !!props.agentId &&
    !enqueueing.value &&
    !hasWaitingHuman.value &&
    !awaitingHostSession.value,
)
const showJumpToBottom = computed(() => !stickToBottom.value && (loading.value || hasRunningTurn.value))
const composerPlaceholder = computed(() => {
  if (awaitingHostSession.value) return '正在等待业务侧创建会话…'
  if (hasWaitingHuman.value) return '请先完成上方确认/审批后再发送新消息'
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
  loading.value = false
  activeRunId.value = null
  stopPolling()
  focusComposer()
}

function onHostMessage(event: MessageEvent) {
  const data = event.data
  if (!data || data.source !== 'agent-factory-host') return
  if (data.type === 'set_session' && data.sessionId) {
    void applyHostSession(String(data.sessionId))
  } else if (data.type === 'clear_session') {
    clearHostSession()
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
  if (turn.humanAsk && turn.status === 'waiting_human') h += 150
  if (turn.humanApproval && turn.status === 'waiting_human') {
    h += turn.humanApproval.editMode ? 280 : 200
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

function onListScroll() {
  const el = listRef.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  stickToBottom.value = distance <= BOTTOM_THRESHOLD_PX
}

async function scrollToBottom(force = false) {
  if (!force && !stickToBottom.value) return
  await nextTick()
  if (!turns.value.length) return
  rowVirtualizer.value.scrollToIndex(turns.value.length - 1, {
    align: 'end',
    behavior: 'auto',
  })
  // After first paint / measure, pin hard to bottom (streaming deltas).
  requestAnimationFrame(() => {
    const el = listRef.value
    if (!el || (!force && !stickToBottom.value)) return
    el.scrollTop = el.scrollHeight
  })
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
  if (running) {
    loading.value = true
    activeRunId.value = running.runId || running.id
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
    turns.value = timelineToTurns(timeline.runs || [])
    await refreshQueue(sessionId)
    const running = turns.value.find((t) => t.status === 'running')
    const waiting = turns.value.find((t) => t.status === 'waiting_human')
    if (running) {
      loading.value = true
      activeRunId.value = running.runId || running.id
      startTimelinePoll()
    } else if (waiting) {
      loading.value = false
      activeRunId.value = null
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
    if (
      typeof event.content === 'string' &&
      event.content.length >= turn.assistantContent.length
    ) {
      turn.assistantContent = event.content
    }
  } else if (type === 'tool_call') {
    const callId = String(event.call_id || Date.now())
    const existing = turn.tools.find((t) => t.callId === callId)
    if (!existing) {
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
    activeRunId.value = null
  } else if (type === 'tool_approval_required') {
    const args = event.arguments
    turn.status = 'waiting_human'
    turn.humanAsk = undefined
    turn.humanApproval = {
      callId: String(event.call_id || ''),
      name: String(event.name || 'tool'),
      arguments: args ?? {},
      expiresAt: event.expires_at ? String(event.expires_at) : null,
      editMode: turn.humanApproval?.editMode || false,
      editedArgumentsJson:
        turn.humanApproval?.editedArgumentsJson ||
        JSON.stringify(args ?? {}, null, 2),
    }
    turn.processExpanded = true
    loading.value = false
    activeRunId.value = null
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

  // Clear streamable fields; hub replay rebuilds them including in-flight deltas.
  // Keep HITL cards when reconnecting a parked waiting_human run.
  if (!wasWaiting) {
    turn.reasoning = ''
    turn.tools = []
    turn.assistantContent = ''
    turn.humanAsk = undefined
    turn.humanApproval = undefined
    turn.status = 'running'
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
    )
    if (hubUnavailable || (getTurn()?.status === 'running' && !sawTerminal)) {
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

async function loadHistory(sessionId: string) {
  const [timeline] = await Promise.all([
    fetchTimeline(props.baseUrl, sessionId, props.apiKey),
    refreshQueue(sessionId),
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

  try {
    await streamResumeRun(
      props.baseUrl,
      runId,
      {
        call_id: approval.callId,
        decision,
        edited_arguments: decision === 'edit' ? editedArguments : undefined,
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

async function stop() {
  userStopped.value = true
  const runId = activeRunId.value
  if (runId) {
    try {
      await cancelRun(props.baseUrl, runId, props.apiKey)
    } catch {
      // ignore
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
  void bootstrap()
})
onBeforeUnmount(() => {
  window.removeEventListener('message', onHostMessage)
  // Only drop the local SSE reader; server run keeps going.
  abortRef.value?.abort()
  stopPolling()
})
watch(
  () => [props.agentId, props.baseUrl],
  () => {
    stopPolling()
    void bootstrap()
  },
)
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
    class="af-chat"
    :class="{ 'af-chat--no-header': !showHeader }"
    v-loading="bootstrapping"
  >
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
      v-if="contextHint && !errorText"
      class="af-chat__alert"
      :title="contextHint"
      type="info"
      show-icon
      :closable="true"
      @close="contextHint = ''"
    />

    <div class="af-chat__list-wrap">
      <div ref="listRef" class="af-chat__list" @scroll.passive="onListScroll">
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
              @toggle-process="toggleProcess(turns[vr.index])"
              @submit-answer="(opt) => void submitHumanAnswer(turns[vr.index], opt)"
              @submit-approval="(d) => void submitHumanApproval(turns[vr.index], d)"
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

    <ChatComposer
      ref="composerRef"
      v-model="input"
      v-model:model-profile-id="selectedModelProfileId"
      :models="models"
      :skills="skills"
      :disabled="bootstrapping || !agentId"
      :loading="loading || hasRunningTurn"
      :submit-disabled="hasWaitingHuman || enqueueing"
      :placeholder="composerPlaceholder"
      @submit="(text) => void send(text)"
      @stop="stop"
    />
  </div>
</template>
