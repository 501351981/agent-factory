<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Plus, Refresh } from '@element-plus/icons-vue'

type SideTab = 'chat' | 'settings'

const CHAT_ORIGIN = import.meta.env.VITE_CHAT_WEB_ORIGIN || 'http://localhost:8801'
const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'
const API_KEY = import.meta.env.VITE_FACTORY_API_KEY || ''

type Agent = { id: string; name: string; access_key?: string | null }
type SessionItem = {
  id: string
  agent_id: string
  title?: string | null
  user_key?: string | null
  status?: string
  updated_at?: string
  created_at?: string
}

type AgentCredentials = { accessKey: string; secretKey: string }

const CREDENTIALS_STORAGE_KEY = 'demo.credentialsByAgent'

function readCredentialsMap(): Record<string, AgentCredentials> {
  try {
    const raw = localStorage.getItem(CREDENTIALS_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Record<string, Partial<AgentCredentials>>
    if (!parsed || typeof parsed !== 'object') return {}
    const out: Record<string, AgentCredentials> = {}
    for (const [id, value] of Object.entries(parsed)) {
      if (!id || !value || typeof value !== 'object') continue
      out[id] = {
        accessKey: String(value.accessKey || ''),
        secretKey: String(value.secretKey || ''),
      }
    }
    return out
  } catch {
    return {}
  }
}

function writeCredentialsMap(map: Record<string, AgentCredentials>) {
  localStorage.setItem(CREDENTIALS_STORAGE_KEY, JSON.stringify(map))
}

function loadCredentialsForAgent(id: string): AgentCredentials {
  if (!id) return { accessKey: '', secretKey: '' }
  const map = readCredentialsMap()
  const hit = map[id]
  if (hit) return { accessKey: hit.accessKey || '', secretKey: hit.secretKey || '' }

  // 兼容旧版全局缓存：只迁移到当前选中的 agent
  const legacyAk = localStorage.getItem('demo.accessKey') || ''
  const legacySk = localStorage.getItem('demo.secretKey') || ''
  if (legacyAk || legacySk) {
    const migrated = { accessKey: legacyAk, secretKey: legacySk }
    map[id] = migrated
    writeCredentialsMap(map)
    localStorage.removeItem('demo.accessKey')
    localStorage.removeItem('demo.secretKey')
    return migrated
  }
  return { accessKey: '', secretKey: '' }
}

function saveCredentialsForAgent(id: string, credentials: AgentCredentials) {
  if (!id) return
  const map = readCredentialsMap()
  map[id] = {
    accessKey: credentials.accessKey || '',
    secretKey: credentials.secretKey || '',
  }
  writeCredentialsMap(map)
}

const agentId = ref(localStorage.getItem('demo.agentId') || '')
const sessionId = ref(localStorage.getItem('demo.sessionId') || '')
const userKey = ref(localStorage.getItem('demo.userKey') || 'demo-user')
const userId = ref(localStorage.getItem('demo.userId') || 'demo-user-001')
const locale = ref(localStorage.getItem('demo.locale') || 'zh')
const initialCredentials = loadCredentialsForAgent(agentId.value)
const accessKey = ref(initialCredentials.accessKey)
const secretKey = ref(initialCredentials.secretKey)
const agents = ref<Agent[]>([])
const sessions = ref<SessionItem[]>([])
const loading = ref(false)
const sessionsLoading = ref(false)
const minting = ref(false)
const embedToken = ref('')
const sideTab = ref<SideTab>('chat')
const notice = ref('选择智能体并配置凭证后，即可嵌入对话。')
/** 避免初始化 watch 覆盖当前输入 */
let agentSwitchReady = false
/** mint 成功后按 Token 纠正 agent 时，跳过 watch 里的二次 mint */
let skipAgentWatchReconnect = false

const selectedAgentName = computed(() => {
  const hit = agents.value.find((a) => a.id === agentId.value)
  return hit?.name || '未选择智能体'
})

const currentSessionLabel = computed(() => {
  if (!sessionId.value) return '新对话'
  const hit = sessions.value.find((s) => s.id === sessionId.value)
  if (hit?.title?.trim()) return hit.title.trim()
  return `会话 ${sessionId.value.slice(0, 8)}…`
})

/** Stable blank embed — sessionId 由宿主 postMessage 注入，避免切会话时丢掉 pending 发送。 */
const embedUrl = computed(() => {
  if (!agentId.value || !embedToken.value) return ''
  const query = new URLSearchParams({
    agentId: agentId.value,
    token: embedToken.value,
    userKey: userKey.value || 'demo-user',
    userId: userId.value || 'demo-user-001',
    locale: locale.value || 'zh',
    deviceId: 'demo-mac',
  })
  return `${CHAT_ORIGIN}/embed?${query.toString()}`
})

/** Remount only when agent/token identity changes — not on session switch. */
const iframeKey = computed(
  () => `${agentId.value}|${embedToken.value.slice(0, 12)}`,
)

const iframeRef = ref<HTMLIFrameElement | null>(null)
const creatingSession = ref(false)

function postToEmbed(payload: Record<string, unknown>) {
  const win = iframeRef.value?.contentWindow
  if (!win) return
  win.postMessage({ source: 'agent-factory-host', ...payload }, CHAT_ORIGIN)
}

function pushSessionToEmbed() {
  if (sessionId.value) {
    postToEmbed({ type: 'set_session', sessionId: sessionId.value })
  } else {
    postToEmbed({ type: 'clear_session' })
  }
}

function persist(options?: { credentials?: boolean }) {
  localStorage.setItem('demo.agentId', agentId.value)
  localStorage.setItem('demo.sessionId', sessionId.value)
  localStorage.setItem('demo.userKey', userKey.value)
  localStorage.setItem('demo.userId', userId.value)
  localStorage.setItem('demo.locale', locale.value)
  // 默认不在通用 persist 里写凭证，避免切 agent 时把旧 AK/SK 写到新 agent
  if (options?.credentials && agentId.value) {
    saveCredentialsForAgent(agentId.value, {
      accessKey: accessKey.value,
      secretKey: secretKey.value,
    })
  }
}

function applyCredentialsForAgent(id: string) {
  const next = loadCredentialsForAgent(id)
  accessKey.value = next.accessKey
  secretKey.value = next.secretKey
}

/** 重新连接成功后：按 agent 缓存本机 AK/SK */
function persistCredentials(id: string, ak: string, sk: string) {
  if (!id || !ak || !sk) return
  saveCredentialsForAgent(id, { accessKey: ak, secretKey: sk })
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  if (embedToken.value) {
    headers.Authorization = `Bearer ${embedToken.value}`
    return headers
  }
  const ak = normalizeCredential(accessKey.value, 'ak')
  const sk = normalizeCredential(secretKey.value, 'sk')
  if (ak && sk) {
    headers['X-Access-Key'] = ak
    headers['X-Secret-Key'] = sk
    return headers
  }
  if (API_KEY) headers['X-API-Key'] = API_KEY
  return headers
}

function normalizeCredential(raw: string, kind: 'ak' | 'sk'): string {
  let text = (raw || '').trim().replace(/^\uFEFF/, '')
  if (kind === 'ak') {
    const m = text.match(/ak_[A-Za-z0-9_-]+/)
    if (m) return m[0]
  } else {
    const m = text.match(/sk_[A-Za-z0-9_-]+/)
    if (m) return m[0]
  }
  return text.replace(/\s+/g, '')
}

function assertHeaderSafe(value: string, label: string): void {
  for (let i = 0; i < value.length; i += 1) {
    if (value.charCodeAt(i) > 255) {
      throw new Error(
        `${label} 含有非 ASCII 字符，不能放进 HTTP Header。请只粘贴 ak_/sk_ 本身。`,
      )
    }
  }
}

function formatSessionTime(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function sessionTitle(item: SessionItem): string {
  const title = (item.title || '').trim()
  if (title) return title
  return `会话 ${item.id.slice(0, 8)}…`
}

async function loadAgents() {
  loading.value = true
  try {
    const headers: Record<string, string> = {}
    if (API_KEY) headers['X-API-Key'] = API_KEY
    const resp = await fetch(`${API_BASE}/api/agents`, { headers })
    if (!resp.ok) throw new Error(await resp.text())
    agents.value = await resp.json()
    if (!agentId.value && agents.value[0]) {
      agentId.value = agents.value[0].id
      persist()
    }
    notice.value = agents.value.length
      ? '已加载智能体。配置 AK/SK 后点「连接」换取嵌入 Token。'
      : '还没有智能体，请先到管理台创建。'
  } catch (err) {
    notice.value = `加载失败：${err instanceof Error ? err.message : String(err)}`
    ElMessage.error(notice.value)
  } finally {
    loading.value = false
  }
}

async function loadSessions() {
  if (!agentId.value) {
    sessions.value = []
    return
  }
  sessionsLoading.value = true
  try {
    const headers = authHeaders()
    const qs = new URLSearchParams({
      agent_id: agentId.value,
      page: '1',
      page_size: '100',
    })
    const resp = await fetch(`${API_BASE}/api/sessions?${qs}`, { headers })
    if (!resp.ok) throw new Error(await resp.text())
    const page = (await resp.json()) as { items?: SessionItem[] }
    const rows = page.items || []
    const uk = (userKey.value || '').trim()
    sessions.value = uk
      ? rows.filter((s) => !s.user_key || s.user_key === uk)
      : rows
  } catch (err) {
    // 未连上 Token 时静默；有凭证再提示
    if (embedToken.value || (accessKey.value && secretKey.value) || API_KEY) {
      ElMessage.warning(
        `会话列表加载失败：${err instanceof Error ? err.message : String(err)}`,
      )
    }
    sessions.value = []
  } finally {
    sessionsLoading.value = false
  }
}

async function mintEmbedToken() {
  const ak = normalizeCredential(accessKey.value, 'ak')
  const sk = normalizeCredential(secretKey.value, 'sk')
  if (!ak || !sk) {
    embedToken.value = ''
    ElMessage.warning('请填写 Access Key 与 Secret Key')
    return
  }
  try {
    assertHeaderSafe(ak, 'Access Key')
    assertHeaderSafe(sk, 'Secret Key')
  } catch (err) {
    embedToken.value = ''
    notice.value = err instanceof Error ? err.message : String(err)
    ElMessage.error(notice.value)
    return
  }
  accessKey.value = ak
  secretKey.value = sk

  minting.value = true
  try {
    const resp = await fetch(`${API_BASE}/api/auth/embed-token`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Access-Key': ak,
        'X-Secret-Key': sk,
      },
      body: JSON.stringify({
        user_key: userKey.value || 'demo-user',
        ttl_seconds: 3600,
      }),
    })
    if (!resp.ok) throw new Error(await resp.text())
    const data = await resp.json()
    const ownerAgentId = String(data.agent_id || agentId.value || '')
    // 连接成功后按凭证所属 agent 落盘（支持多 agent 各自缓存）
    persistCredentials(ownerAgentId, ak, sk)
    if (ownerAgentId && ownerAgentId !== agentId.value) {
      ElMessage.warning('Token 对应的 Agent 与当前选择不一致，已切换到凭证所属 Agent')
      skipAgentWatchReconnect = true
      agentId.value = ownerAgentId
      accessKey.value = ak
      secretKey.value = sk
    }
    embedToken.value = data.embed_token
    persist()
    notice.value = `已连接（user_key=${data.user_key || '无'}），凭证已缓存到本机，过期：${data.expires_at}`
    ElMessage.success('已连接，AK/SK 已按智能体缓存')
    sideTab.value = 'chat'
    await loadSessions()
  } catch (err) {
    embedToken.value = ''
    notice.value = `换取 Token 失败：${err instanceof Error ? err.message : String(err)}`
    ElMessage.error(notice.value)
  } finally {
    minting.value = false
  }
}

function selectSession(id: string) {
  if (sessionId.value === id) return
  sessionId.value = id
  persist()
  notice.value = `已切换到会话 ${id.slice(0, 8)}…`
  postToEmbed({ type: 'set_session', sessionId: id })
}

function newSession() {
  sessionId.value = ''
  persist()
  postToEmbed({ type: 'clear_session' })
  ElMessage.success('已开始新对话')
}

function titleFromMessage(message: unknown): string {
  const text = String(message || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return ''
  return text.length > 40 ? `${text.slice(0, 40)}…` : text
}

function upsertSessionLocal(item: SessionItem) {
  const idx = sessions.value.findIndex((s) => s.id === item.id)
  if (idx >= 0) {
    sessions.value[idx] = { ...sessions.value[idx], ...item }
  } else {
    sessions.value = [item, ...sessions.value]
  }
}

async function createSessionForEmbed(req?: Record<string, unknown>) {
  if (creatingSession.value) return
  if (!agentId.value) {
    ElMessage.warning('请先选择智能体')
    return
  }
  creatingSession.value = true
  try {
    const headers = {
      ...authHeaders(),
      'Content-Type': 'application/json',
    }
    const title =
      String(req?.title || '').trim() || titleFromMessage(req?.message) || undefined
    const body = {
      agent_id: agentId.value,
      user_key: userKey.value || 'demo-user',
      locale: locale.value || 'zh',
      prompt_vars: { userId: userId.value || 'demo-user-001' },
      client_device_id: 'demo-mac',
      ...(title ? { title } : {}),
      // 业务可在此附加自己的元数据；工厂 API 只消费已知字段
      ...(req?.promptVars && typeof req.promptVars === 'object'
        ? { prompt_vars: { userId: userId.value || 'demo-user-001', ...(req.promptVars as object) } }
        : {}),
    }
    const resp = await fetch(`${API_BASE}/api/sessions`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    })
    if (!resp.ok) throw new Error(await resp.text())
    const created = (await resp.json()) as SessionItem
    sessionId.value = created.id
    persist()
    notice.value = `业务侧已创建会话 ${created.id.slice(0, 8)}…`
    upsertSessionLocal({
      ...created,
      title: created.title || title || '新会话',
      agent_id: created.agent_id || agentId.value,
      updated_at: created.updated_at || new Date().toISOString(),
    })
    postToEmbed({ type: 'set_session', sessionId: created.id })
    void loadSessions()
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    notice.value = `创建会话失败：${msg}`
    ElMessage.error(notice.value)
    postToEmbed({ type: 'session_create_failed', error: msg })
  } finally {
    creatingSession.value = false
  }
}

function onMessage(event: MessageEvent) {
  if (event.origin !== CHAT_ORIGIN) return
  const data = event.data
  if (!data || data.source !== 'agent-factory') return

  if (data.type === 'ready') {
    pushSessionToEmbed()
    return
  }

  if (data.type === 'session_create_request') {
    void createSessionForEmbed(data as Record<string, unknown>)
    return
  }

  if (data.type === 'session') {
    const next = String(data.sessionId || '')
    if (!next) return
    const title = String(data.title || '').trim()
    const changed = next !== sessionId.value
    sessionId.value = next
    persist()
    if (title) {
      upsertSessionLocal({
        id: next,
        agent_id: agentId.value,
        title,
        updated_at: new Date().toISOString(),
      })
    }
    if (changed || title) {
      notice.value = title
        ? `已同步会话：${title}`
        : `已同步会话：${next.slice(0, 8)}…`
      void loadSessions()
    }
  }
}

watch(agentId, (nextId, prevId) => {
  if (!agentSwitchReady) return
  const skipReconnect = skipAgentWatchReconnect
  skipAgentWatchReconnect = false
  // 切走前：若当前输入完整，先缓存到旧 agent（含未点连接的草稿）
  if (prevId && accessKey.value && secretKey.value) {
    persistCredentials(prevId, accessKey.value, secretKey.value)
  }
  embedToken.value = ''
  sessionId.value = ''
  sessions.value = []
  // 再从缓存恢复目标 agent 的凭证
  applyCredentialsForAgent(nextId)
  persist()
  // 不自动 mint / 不自动跳到「对话」：等用户点「连接嵌入」
  if (skipReconnect) return
  sideTab.value = 'settings'
  if (accessKey.value && secretKey.value) {
    notice.value = '已自动填充该智能体的 AK/SK，请点击「连接嵌入」。'
  } else {
    notice.value = '该智能体尚未缓存凭证，请填写 AK/SK 后点击「连接嵌入」。'
  }
  void loadSessions()
})

watch(userKey, () => {
  void loadSessions()
})

onMounted(() => {
  window.addEventListener('message', onMessage)
  void loadAgents().then(() => {
    applyCredentialsForAgent(agentId.value)
    agentSwitchReady = true
    if (accessKey.value && secretKey.value) {
      void mintEmbedToken()
    } else {
      void loadSessions()
    }
  })
})

onUnmounted(() => {
  window.removeEventListener('message', onMessage)
})
</script>

<template>
  <div class="demo">
    <aside class="demo__side">
      <div class="demo__brand">
        <span class="demo__mark" aria-hidden="true">
          <el-icon :size="16"><ChatDotRound /></el-icon>
        </span>
        <div class="demo__brand-text">
          <div class="demo__brand-name">业务 Demo</div>
          <div class="demo__brand-sub">iframe 嵌入 Agent 工厂</div>
        </div>
      </div>

      <div class="demo__seg" role="tablist" aria-label="侧栏页签">
        <button
          type="button"
          role="tab"
          class="demo__seg-item"
          :class="{ 'is-active': sideTab === 'chat' }"
          :aria-selected="sideTab === 'chat'"
          @click="sideTab = 'chat'"
        >
          对话
        </button>
        <button
          type="button"
          role="tab"
          class="demo__seg-item"
          :class="{ 'is-active': sideTab === 'settings' }"
          :aria-selected="sideTab === 'settings'"
          @click="sideTab = 'settings'"
        >
          设置
        </button>
      </div>

      <div v-show="sideTab === 'chat'" class="demo__panel demo__sessions">
        <div class="demo__section-head">
          <span>会话</span>
          <button type="button" class="demo__icon-btn" title="刷新会话" @click="loadSessions">
            <el-icon :size="16"><Refresh /></el-icon>
          </button>
        </div>

        <button type="button" class="demo__new" @click="newSession">
          <el-icon><Plus /></el-icon>
          新对话
        </button>

        <div class="demo__session-list" v-loading="sessionsLoading">
          <button
            v-for="item in sessions"
            :key="item.id"
            type="button"
            class="demo__session"
            :class="{ 'is-active': sessionId === item.id }"
            :title="item.id"
            @click="selectSession(item.id)"
          >
            <span class="demo__session-title">{{ sessionTitle(item) }}</span>
            <span class="demo__session-meta">
              {{ formatSessionTime(item.updated_at || item.created_at) }}
            </span>
          </button>

          <p v-if="!sessionsLoading && !sessions.length" class="demo__empty">
            暂无历史会话，发送消息后会出现在这里
          </p>
        </div>
      </div>

      <div v-show="sideTab === 'settings'" class="demo__panel demo__settings">
        <div class="demo__settings-body">
          <p class="demo__notice">{{ notice }}</p>

          <label class="demo__field">
            <span>智能体</span>
            <el-select
              v-model="agentId"
              placeholder="请选择"
              filterable
              style="width: 100%"
              :loading="loading"
            >
              <el-option
                v-for="agent in agents"
                :key="agent.id"
                :label="agent.name"
                :value="agent.id"
              />
            </el-select>
          </label>

          <label class="demo__field">
            <span>用户 Key</span>
            <el-input v-model="userKey" placeholder="demo-user" @change="persist" />
          </label>

          <label class="demo__field">
            <span>userId（prompt 变量）</span>
            <el-input v-model="userId" placeholder="demo-user-001" @change="persist" />
          </label>

          <label class="demo__field">
            <span>语言</span>
            <el-select v-model="locale" style="width: 100%" @change="persist">
              <el-option label="中文 zh" value="zh" />
              <el-option label="English en" value="en" />
              <el-option label="日本語 ja" value="ja" />
            </el-select>
          </label>

          <label class="demo__field">
            <span>Access Key</span>
            <el-input
              v-model="accessKey"
              placeholder="ak_..."
              @change="persistCredentials(agentId, accessKey, secretKey)"
            />
          </label>

          <label class="demo__field">
            <span>Secret Key</span>
            <el-input
              v-model="secretKey"
              placeholder="sk_...（按智能体缓存在本机）"
              type="password"
              show-password
              @change="persistCredentials(agentId, accessKey, secretKey)"
            />
          </label>
        </div>

        <div class="demo__settings-footer">
          <button
            type="button"
            class="demo__connect"
            :disabled="minting"
            @click="mintEmbedToken"
          >
            {{ minting ? '连接中…' : embedToken ? '重新连接' : '连接嵌入' }}
          </button>
        </div>
      </div>
    </aside>

    <main class="demo__main">
      <header class="demo__chat-bar">
        <div class="demo__chat-bar-left">
          <div class="demo__chat-title">{{ currentSessionLabel }}</div>
          <div class="demo__chat-sub">
            {{ selectedAgentName }}
            <span v-if="sessionId" class="demo__dot" />
            <span v-if="sessionId" class="demo__mono">{{ sessionId.slice(0, 8) }}…</span>
          </div>
        </div>
        <div class="demo__chat-bar-actions">
          <button type="button" class="demo__ghost-btn" @click="newSession">新对话</button>
          <button
            type="button"
            class="demo__ghost-btn"
            :disabled="!embedToken"
            @click="loadSessions"
          >
            刷新列表
          </button>
        </div>
      </header>

      <div class="demo__chat-body">
        <iframe
          v-if="embedUrl"
          ref="iframeRef"
          :key="iframeKey"
          class="demo__frame"
          :src="embedUrl"
          title="agent-factory-embed"
          allow="clipboard-read; clipboard-write"
          @load="pushSessionToEmbed"
        />
        <div v-else class="demo__placeholder">
          <div class="demo__placeholder-mark" aria-hidden="true">
            <el-icon :size="28"><ChatDotRound /></el-icon>
          </div>
          <h2>准备嵌入对话</h2>
          <p>在左侧填写 AK/SK，点击「连接嵌入」后即可在此预览聊天页。</p>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.demo {
  --demo-bg: #f7f7f8;
  --demo-surface: #ffffff;
  --demo-border: rgba(0, 0, 0, 0.06);
  --demo-text: #0d0d0d;
  --demo-muted: #8e8e8e;
  height: 100%;
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  background: var(--demo-bg);
  color: var(--demo-text);
  font-family:
    'PingFang SC',
    'Hiragino Sans GB',
    'Noto Sans SC',
    'Segoe UI',
    system-ui,
    sans-serif;
  -webkit-font-smoothing: antialiased;
}

.demo__side {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--demo-surface);
  border-right: 1px solid var(--demo-border);
}

.demo__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 16px 12px;
  flex-shrink: 0;
}

.demo__mark {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: linear-gradient(145deg, #1a1a1a 0%, #3f3f46 100%);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.demo__brand-name {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.02em;
  line-height: 1.2;
}

.demo__brand-sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--demo-muted);
}

.demo__seg {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px;
  margin: 4px 14px 12px;
  padding: 3px;
  border-radius: 999px;
  background: #ececf0;
  flex-shrink: 0;
}

.demo__seg-item {
  height: 34px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: #525252;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background 0.15s ease,
    color 0.15s ease,
    box-shadow 0.15s ease;
}

.demo__seg-item.is-active {
  background: #fff;
  color: var(--demo-text);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.demo__seg-item:hover:not(.is-active) {
  color: var(--demo-text);
}

.demo__panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 10px 12px;
  overflow: hidden;
}

.demo__sessions {
  /* inherits panel */
}

.demo__settings {
  overflow: hidden;
  padding: 0;
}

.demo__settings-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 16px 12px;
}

.demo__settings-footer {
  flex-shrink: 0;
  padding: 12px 16px 16px;
  border-top: 1px solid var(--demo-border);
  background: var(--demo-surface);
}

.demo__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--demo-muted);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.demo__icon-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #737373;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.demo__icon-btn:hover {
  background: rgba(0, 0, 0, 0.05);
  color: var(--demo-text);
}

.demo__new {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 40px;
  margin: 0 2px 10px;
  border: 1px dashed #d4d4d4;
  border-radius: 12px;
  background: transparent;
  color: var(--demo-text);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background 0.15s ease,
    border-color 0.15s ease;
}

.demo__new:hover {
  background: #fafafa;
  border-color: #a3a3a3;
}

.demo__session-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-bottom: 8px;
}

.demo__session {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 10px 12px;
  margin-bottom: 2px;
  border: none;
  border-radius: 10px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}

.demo__session:hover {
  background: #f5f5f5;
}

.demo__session.is-active {
  background: #f0f0f0;
}

.demo__session-title {
  width: 100%;
  font-size: 14px;
  font-weight: 500;
  color: var(--demo-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.demo__session-meta {
  font-size: 12px;
  color: var(--demo-muted);
}

.demo__empty {
  margin: 16px 8px;
  font-size: 13px;
  color: var(--demo-muted);
  line-height: 1.5;
  text-align: center;
}

.demo__notice {
  margin: 0 0 14px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f5f5f5;
  border: 1px solid var(--demo-border);
  font-size: 13px;
  color: #525252;
  line-height: 1.5;
}

.demo__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #404040;
  font-weight: 500;
}

.demo__field :deep(.el-input__wrapper),
.demo__field :deep(.el-select__wrapper) {
  border-radius: 10px;
  box-shadow: 0 0 0 1px #e5e5e5 inset;
}

.demo__connect {
  width: 100%;
  height: 40px;
  margin: 0;
  border: none;
  border-radius: 12px;
  background: #0d0d0d;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}

.demo__connect:hover:not(:disabled) {
  background: #262626;
}

.demo__connect:disabled {
  opacity: 0.55;
  cursor: default;
}

.demo__main {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.demo__chat-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 20px;
  background: rgba(247, 247, 248, 0.9);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--demo-border);
  flex-shrink: 0;
}

.demo__chat-title {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.02em;
}

.demo__chat-sub {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-size: 12px;
  color: var(--demo-muted);
}

.demo__dot {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #c4c4c4;
}

.demo__mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.demo__chat-bar-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.demo__ghost-btn {
  height: 34px;
  padding: 0 12px;
  border: 1px solid #e5e5e5;
  border-radius: 10px;
  background: #fff;
  color: #404040;
  font-size: 13px;
  cursor: pointer;
  transition:
    background 0.15s ease,
    border-color 0.15s ease;
}

.demo__ghost-btn:hover:not(:disabled) {
  background: #fafafa;
  border-color: #d4d4d4;
}

.demo__ghost-btn:disabled {
  opacity: 0.45;
  cursor: default;
}

.demo__chat-body {
  flex: 1;
  min-height: 0;
  display: flex;
  background: var(--demo-bg);
}

.demo__frame {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
  background: var(--demo-bg);
}

.demo__placeholder {
  margin: auto;
  text-align: center;
  padding: 40px 24px;
  max-width: 360px;
}

.demo__placeholder-mark {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  border-radius: 16px;
  background: linear-gradient(145deg, #1a1a1a 0%, #3f3f46 100%);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

.demo__placeholder h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.03em;
}

.demo__placeholder p {
  margin: 10px 0 0;
  font-size: 14px;
  color: var(--demo-muted);
  line-height: 1.6;
}

@media (max-width: 900px) {
  .demo {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(240px, 38vh) minmax(0, 1fr);
  }

  .demo__side {
    border-right: none;
    border-bottom: 1px solid var(--demo-border);
  }
}
</style>
