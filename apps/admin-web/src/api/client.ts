import { API_BASE, API_KEY } from '../env'
import { getAdminToken, redirectToLogin } from '../auth'

/** 认证头：管理台登录 token 优先，其次回退到构建期 VITE_FACTORY_API_KEY。 */
export function authHeaders(): Record<string, string> {
  const token = getAdminToken()
  if (token) return { Authorization: `Bearer ${token}` }
  if (API_KEY) return { 'X-API-Key': API_KEY }
  return {}
}

function headers(): HeadersInit {
  return { 'Content-Type': 'application/json', ...authHeaders() }
}

type RequestOptions = RequestInit & { skipAuthRedirect?: boolean }

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { skipAuthRedirect, ...rest } = init || {}
  const resp = await fetch(`${API_BASE}${path}`, {
    ...rest,
    headers: { ...headers(), ...(rest.headers || {}) },
  })
  if (resp.status === 401 && !skipAuthRedirect) {
    redirectToLogin()
  }
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  if (resp.status === 204) return undefined as T
  return resp.json()
}

export type MemoryPolicy = {
  enabled?: boolean
  profile_top_k?: number
  playbook_top_k?: number
  max_chars?: number
  max_profile_items?: number
  max_playbook_items?: number
  max_profile_chars?: number
  max_playbook_chars?: number
  max_detail_chars?: number
  evict_when_full?: boolean
}

export type KnowledgePolicy = {
  enabled?: boolean
  max_catalog_chars?: number
  include_user_docs?: boolean
  max_list_items?: number
}

export type MultiAgentPolicy = {
  enabled?: boolean
  team_id?: string | null
}

export type SchedulerPolicy = {
  enabled?: boolean
  max_active_jobs?: number
}

export type AgentRuntime = {
  max_tool_rounds?: number
  on_max_rounds?: 'summarize' | 'stop'
  max_run_tokens?: number
  tool_timeout_seconds?: number
  run_timeout_seconds?: number
  parallel_tool_calls?: boolean
  tool_result_max_chars?: number
  memory?: MemoryPolicy
  knowledge?: KnowledgePolicy
  multi_agent?: MultiAgentPolicy
  scheduler?: SchedulerPolicy
}

export type Schedule = {
  id: string
  agent_id: string
  user_key: string
  created_from_session_id: string
  created_by_run_id?: string | null
  name: string
  schedule_kind: string
  run_at?: string | null
  every_ms?: number | null
  cron_expr?: string | null
  timezone: string
  session_target: string
  session_id?: string | null
  run_mode: string
  payload_text: string
  status: string
  delete_after_run: boolean
  next_run_at?: string | null
  last_run_at?: string | null
  last_error?: string | null
  attempt_count: number
  last_fired_session_id?: string | null
  last_fired_run_id?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export type ScheduleStats = {
  agent_id: string
  user_key?: string | null
  active: number
  paused: number
  completed: number
  cancelled: number
  failed: number
  total: number
  max_active_jobs: number
  due_soon: number
}

export type TeamMember = {
  agent_id: string
  role: string
}

export type AgentTeam = {
  id: string
  name: string
  lead_agent_id: string
  members: TeamMember[]
  max_depth: number
  max_parallel: number
  max_child_rounds: number
  result_max_chars: number
  allow_async: boolean
  enabled: boolean
  created_at: string
  updated_at: string
}

export type BoardArtifact = {
  id: string
  project_id: string
  path: string
  title: string
  content: string
  version: number
  author_agent_id: string
  status: string
  created_at: string
  updated_at: string
}

export type BoardProject = {
  id: string
  lead_session_id: string
  team_id?: string | null
  title: string
  user_key: string
  status: string
  artifacts: BoardArtifact[]
  created_at: string
  updated_at: string
}

export type ChildRun = {
  run_id: string
  session_id: string
  agent_id: string
  status: string
  user_input: string
  parent_run_id?: string | null
  root_session_id?: string | null
  delegation_call_id?: string | null
  delegation_depth: number
  finish_reason?: string | null
  error_message?: string | null
  started_at: string
  finished_at?: string | null
  summary?: string | null
}

export type KbDocument = {
  id: string
  /** 归属的 Agent：所有文档都挂在某个 Agent 下 */
  agent_id: string
  /** agent = 该 Agent 内共享；user = 该 Agent 下某个终端用户 */
  scope: 'agent' | 'user' | string
  /** 仅 scope=user 时有值 */
  user_key: string
  path: string
  title: string
  summary: string
  summary_source: 'auto' | 'llm' | 'manual' | string
  size_bytes: number
  sha256: string
  status: string
  chars: number
  created_at: string
  updated_at: string
}

export type KbDocumentPage = {
  items: KbDocument[]
  total: number
  page: number
  page_size: number
  /** scope=user 时返回该 Agent 下出现过的全部 user_key */
  user_keys: string[]
}

export type KbDocumentContent = {
  id: string
  path: string
  content: string
  total_chars: number
  truncated: boolean
}

export type KbUploadResult = {
  document: KbDocument
  created: boolean
  summary_source: string
  warnings: string[]
}

export type KbFolderResult = {
  affected: number
}

export type MemoryUsage = {
  agent_id: string
  user_key: string
  profile_active: number
  profile_inactive: number
  playbook_active: number
  playbook_inactive: number
  detail_active: number
  detail_inactive: number
  approx_chars: number
  quotas: MemoryPolicy
}

export type Agent = {
  id: string
  name: string
  system_prompt: string
  system_prompts?: Record<string, string>
  model?: string | null
  model_profile_id?: string | null
  model_profile_name?: string | null
  tools: unknown[]
  skills: unknown[]
  runtime?: AgentRuntime
  logo_url?: string | null
  welcome_title?: string | null
  welcome_subtitle?: string | null
  suggested_prompts?: string[]
  /** Chat UI theme tokens (colors / radii). */
  ui_theme?: Record<string, string> | null
  access_key?: string | null
  has_secret_key?: boolean
  created_at: string
  updated_at: string
}

export type SkillPackageFile = {
  path: string
  kind: string
  is_binary: boolean
  size_bytes: number
  sha256: string
  mode?: string | null
}

export type SkillPackage = {
  id: string
  agent_id: string
  name: string
  description: string
  version?: string | null
  source: string
  status: string
  total_bytes: number
  file_count: number
  files: SkillPackageFile[]
  warnings: string[]
  created_at: string
  updated_at: string
}

export type PlatformSkillPackage = {
  id: string
  name: string
  description: string
  version?: string | null
  source: string
  status: string
  total_bytes: number
  file_count: number
  files: SkillPackageFile[]
  warnings: string[]
  mount_count: number
  created_at: string
  updated_at: string
}

export type AgentSkillMount = {
  agent_id: string
  package_id: string
  name: string
  description: string
  version?: string | null
  source: string
  status: string
  total_bytes: number
  file_count: number
  files: SkillPackageFile[]
  created_at: string
  mounted_at: string
}

export type AgentCredential = {
  agent_id: string
  access_key: string
  secret_key: string
  has_secret_key: boolean
}

export type ModelProfile = {
  id: string
  name: string
  provider: string
  base_url: string
  api_key_masked: string
  has_api_key: boolean
  model_name: string
  temperature: number
  context_window_tokens?: number
  reserve_output_tokens?: number
  input_price_per_1m?: number
  output_price_per_1m?: number
  is_default: boolean
  enabled: boolean
  remark?: string | null
  created_at: string
  updated_at: string
}

export type UsageSummaryBucket = {
  key: string
  label: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  uncached_prompt_tokens: number
  estimated_cost: number
}

export type UsageSummary = {
  group_by: string
  from_time?: string | null
  to_time?: string | null
  totals: UsageSummaryBucket
  buckets: UsageSummaryBucket[]
  note?: string
}

export type LlmUsage = {
  id: string
  session_id: string
  run_id: string
  agent_id?: string | null
  model_profile_id?: string | null
  model_name: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  uncached_prompt_tokens: number
  source: string
  input_price_per_1m: number
  output_price_per_1m: number
  estimated_cost: number
  created_at: string
}

export type ModelPreset = {
  provider: string
  label: string
  base_url: string
  model_name: string
  context_window_tokens?: number
  reserve_output_tokens?: number
}

export type RemoteModelInfo = {
  id: string
  context_window_tokens?: number | null
  owned_by?: string | null
  raw_keys?: string[]
}

export type DiscoverModelsResult = {
  models: RemoteModelInfo[]
  note?: string
}

export type Session = {
  id: string
  agent_id: string
  title?: string | null
  user_key?: string | null
  status?: string
  locale?: string
  system_prompt?: string | null
  prompt_vars?: Record<string, string>
  history_cleared_at?: string | null
  created_at: string
  updated_at: string
}

export type SessionPage = {
  items: Session[]
  total: number
  page: number
  page_size: number
}

export type LlmUsagePage = {
  items: LlmUsage[]
  total: number
  page: number
  page_size: number
}

export type Memory = {
  id: string
  agent_id: string
  kind: string
  key: string
  content: string
  user_key?: string | null
  active?: boolean
  pinned?: boolean
  metadata?: Record<string, unknown> | null
  source_run_id?: string | null
  created_at: string
  updated_at: string
}

export type MemoryPage = {
  items: Memory[]
  total: number
  page: number
  page_size: number
}

export type Message = {
  id: string
  session_id: string
  role: string
  content?: string | null
  created_at: string
}

export function resolveMediaUrl(url?: string | null): string {
  const value = (url || '').trim()
  if (!value) return ''
  if (/^(https?:|data:|blob:)/i.test(value)) return value
  const base = API_BASE.replace(/\/$/, '')
  return value.startsWith('/') ? `${base}${value}` : `${base}/${value}`
}

export const authApi = {
  login: async (username: string, password: string) => {
    const resp = await fetch(`${API_BASE}/api/auth/admin-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!resp.ok) {
      const text = await resp.text()
      let message = text
      try {
        const parsed = JSON.parse(text) as { detail?: unknown }
        if (typeof parsed.detail === 'string') message = parsed.detail
      } catch {
        /* 保留原始文本 */
      }
      if (resp.status === 429) throw new Error(message || '尝试过于频繁，请稍后再试')
      throw new Error(message || `HTTP ${resp.status}`)
    }
    return resp.json() as Promise<{
      token: string
      username: string
      expires_at: string
      token_type: string
    }>
  },
  session: () =>
    request<{ token: string; username: string; expires_at: string; token_type: string }>(
      '/api/auth/admin-session',
      { skipAuthRedirect: true },
    ),
}

export const api = {
  listAgents: () => request<Agent[]>('/api/agents'),
  getAgent: (id: string) => request<Agent>(`/api/agents/${id}`),
  createAgent: (body: unknown) =>
    request<Agent>('/api/agents', { method: 'POST', body: JSON.stringify(body) }),
  updateAgent: (id: string, body: unknown) =>
    request<Agent>(`/api/agents/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteAgent: (id: string) =>
    request<void>(`/api/agents/${id}`, { method: 'DELETE' }),
  uploadLogo: async (file: File) => {
    const body = new FormData()
    body.append('file', file)
    const headers: HeadersInit = { ...authHeaders() }
    const resp = await fetch(`${API_BASE}/api/uploads/logo`, {
      method: 'POST',
      headers,
      body,
    })
    if (!resp.ok) throw new Error(await resp.text())
    return resp.json() as Promise<{
      url: string
      filename: string
      content_type: string
      size: number
    }>
  },
  rotateAgentCredentials: (id: string) =>
    request<AgentCredential>(`/api/agents/${id}/credentials/rotate`, { method: 'POST' }),
  revokeAgentCredentials: (id: string) =>
    request<Agent>(`/api/agents/${id}/credentials/revoke`, { method: 'POST' }),
  listSkillPackages: (agentId: string) =>
    request<SkillPackage[]>(`/api/agents/${agentId}/skill-packages`),
  updateSkillPackage: (agentId: string, name: string, body: { description: string }) =>
    request<SkillPackage>(
      `/api/agents/${agentId}/skill-packages/${encodeURIComponent(name)}`,
      { method: 'PATCH', body: JSON.stringify(body) },
    ),
  uploadSkillPackage: async (
    agentId: string,
    file: File,
    opts: { force?: boolean; keepDescription?: boolean; expectedName?: string } = {},
  ) => {
    const body = new FormData()
    body.append('file', file)
    body.append('force', opts.force ? 'true' : 'false')
    body.append('keep_description', opts.keepDescription ? 'true' : 'false')
    if (opts.expectedName) body.append('expected_name', opts.expectedName)
    const headers: HeadersInit = { ...authHeaders() }
    const resp = await fetch(`${API_BASE}/api/agents/${agentId}/skill-packages`, {
      method: 'POST',
      headers,
      body,
    })
    if (!resp.ok) {
      const text = await resp.text()
      try {
        const parsed = JSON.parse(text) as { detail?: unknown }
        const detail = parsed.detail
        if (detail && typeof detail === 'object' && detail !== null && 'message' in detail) {
          throw new Error(String((detail as { message: string }).message))
        }
        if (typeof detail === 'string') throw new Error(detail)
      } catch (err) {
        if (err instanceof Error && err.message && !err.message.startsWith('{')) throw err
      }
      throw new Error(text || `HTTP ${resp.status}`)
    }
    return resp.json() as Promise<SkillPackage>
  },
  deleteSkillPackage: (agentId: string, name: string) =>
    request<void>(`/api/agents/${agentId}/skill-packages/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
  listPlatformSkills: () => request<PlatformSkillPackage[]>('/api/platform-skills'),
  updatePlatformSkill: (name: string, body: { description: string }) =>
    request<PlatformSkillPackage>(`/api/platform-skills/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  uploadPlatformSkill: async (
    file: File,
    opts: { force?: boolean; keepDescription?: boolean } = {},
  ) => {
    const body = new FormData()
    body.append('file', file)
    body.append('force', opts.force ? 'true' : 'false')
    body.append('keep_description', opts.keepDescription ? 'true' : 'false')
    const headers: HeadersInit = { ...authHeaders() }
    const resp = await fetch(`${API_BASE}/api/platform-skills`, {
      method: 'POST',
      headers,
      body,
    })
    if (!resp.ok) {
      const text = await resp.text()
      try {
        const parsed = JSON.parse(text) as { detail?: unknown }
        const detail = parsed.detail
        if (detail && typeof detail === 'object' && detail !== null && 'message' in detail) {
          throw new Error(String((detail as { message: string }).message))
        }
        if (typeof detail === 'string') throw new Error(detail)
      } catch (err) {
        if (err instanceof Error && err.message && !err.message.startsWith('{')) throw err
      }
      throw new Error(text || `HTTP ${resp.status}`)
    }
    return resp.json() as Promise<PlatformSkillPackage>
  },
  deletePlatformSkill: (name: string) =>
    request<void>(`/api/platform-skills/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  listSkillMounts: (agentId: string) =>
    request<AgentSkillMount[]>(`/api/agents/${agentId}/skill-mounts`),
  mountPlatformSkill: (agentId: string, name: string) =>
    request<AgentSkillMount>(
      `/api/agents/${agentId}/skill-mounts/${encodeURIComponent(name)}`,
      { method: 'POST' },
    ),
  unmountPlatformSkill: (agentId: string, name: string) =>
    request<void>(`/api/agents/${agentId}/skill-mounts/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
  listKnowledgeDocuments: (
    agentId: string,
    params: {
      scope?: 'agent' | 'user'
      /** 省略 = 个人文档下展示全部用户 */
      userKey?: string
      pathPrefix?: string
      keyword?: string
      page?: number
      pageSize?: number
      includeDisabled?: boolean
    } = {},
  ) => {
    const query = new URLSearchParams()
    query.set('scope', params.scope || 'agent')
    if (params.userKey) query.set('user_key', params.userKey)
    if (params.pathPrefix) query.set('path_prefix', params.pathPrefix)
    if (params.keyword) query.set('keyword', params.keyword)
    if (params.page) query.set('page', String(params.page))
    if (params.pageSize) query.set('page_size', String(params.pageSize))
    if (params.includeDisabled) query.set('include_disabled', 'true')
    return request<KbDocumentPage>(
      `/api/agents/${agentId}/knowledge/documents?${query.toString()}`,
    )
  },
  getKnowledgeDocumentContent: (agentId: string, docId: string, maxChars = 8000) =>
    request<KbDocumentContent>(
      `/api/agents/${agentId}/knowledge/documents/${docId}/content?max_chars=${maxChars}`,
    ),
  updateKnowledgeDocument: (
    agentId: string,
    docId: string,
    body: { title?: string; summary?: string; path?: string; status?: string },
  ) =>
    request<KbDocument>(`/api/agents/${agentId}/knowledge/documents/${docId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteKnowledgeDocument: (agentId: string, docId: string) =>
    request<void>(`/api/agents/${agentId}/knowledge/documents/${docId}`, {
      method: 'DELETE',
    }),
  uploadKnowledgeDocument: async (
    agentId: string,
    file: File,
    opts: { scope?: 'agent' | 'user'; userKey?: string; dir?: string; summary?: string } = {},
  ) => {
    const body = new FormData()
    body.append('file', file)
    body.append('scope', opts.scope || 'agent')
    if (opts.userKey) body.append('user_key', opts.userKey)
    if (opts.dir) body.append('dir', opts.dir)
    if (opts.summary) body.append('summary', opts.summary)
    const headers: HeadersInit = { ...authHeaders() }
    const resp = await fetch(`${API_BASE}/api/agents/${agentId}/knowledge/documents`, {
      method: 'POST',
      headers,
      body,
    })
    if (!resp.ok) {
      const text = await resp.text()
      try {
        const parsed = JSON.parse(text) as { detail?: unknown }
        const detail = parsed.detail
        if (typeof detail === 'string') throw new Error(detail)
      } catch (err) {
        if (err instanceof Error && err.message && !err.message.startsWith('{')) throw err
      }
      throw new Error(text || `HTTP ${resp.status}`)
    }
    return resp.json() as Promise<KbUploadResult>
  },
  moveKnowledgeFolder: (
    agentId: string,
    body: { scope: 'agent' | 'user'; user_key?: string; path: string; new_path: string },
  ) =>
    request<KbFolderResult>(`/api/agents/${agentId}/knowledge/folders/move`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  deleteKnowledgeFolder: (
    agentId: string,
    body: { scope: 'agent' | 'user'; user_key?: string; path: string },
  ) =>
    request<KbFolderResult>(`/api/agents/${agentId}/knowledge/folders/delete`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  listSessions: (params?: {
    agentId?: string
    page?: number
    pageSize?: number
    includeArchived?: boolean
  }) => {
    const q = new URLSearchParams()
    if (params?.agentId) q.set('agent_id', params.agentId)
    if (params?.page) q.set('page', String(params.page))
    if (params?.pageSize) q.set('page_size', String(params.pageSize))
    if (params?.includeArchived) q.set('include_archived', 'true')
    const qs = q.toString()
    return request<SessionPage>(`/api/sessions${qs ? `?${qs}` : ''}`)
  },
  deleteSession: (sessionId: string, hard = true) => {
    const q = hard ? '?hard=true' : ''
    return request<Session>(`/api/sessions/${sessionId}${q}`, { method: 'DELETE' })
  },
  listMessages: (sessionId: string) =>
    request<Message[]>(`/api/sessions/${sessionId}/messages`),
  debugLlmMessages: (sessionId: string) =>
    request<{
      session_id: string
      agent_system_prompt: string
      memory_block: string
      context_summary: string
      messages: Array<{ role: string; content?: string | null }>
      report: Record<string, unknown>
      prompt_snapshots: Array<{
        id: string
        run_id: string
        created_at: string
        agent_system_prompt: string
        memory_block: string
        context_summary: string
        message_count: number
        estimated_tokens_after?: number | null
        layers_applied: string[]
      }>
      note?: string
    }>(`/api/sessions/${sessionId}/debug/llm-messages`),
  listMemories: (params?: {
    agent_id?: string
    user_key?: string
    kind?: string
    page?: number
    pageSize?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.agent_id) q.set('agent_id', params.agent_id)
    if (params?.user_key) q.set('user_key', params.user_key)
    if (params?.kind) q.set('kind', params.kind)
    if (params?.page) q.set('page', String(params.page))
    if (params?.pageSize) q.set('page_size', String(params.pageSize))
    const qs = q.toString()
    return request<MemoryPage>(`/api/memories${qs ? `?${qs}` : ''}`)
  },
  deleteMemory: (id: string) =>
    request<void>(`/api/memories/${id}`, { method: 'DELETE' }),
  memoryUsage: (agentId: string, userKey: string) =>
    request<MemoryUsage>(
      `/api/memories/usage?agent_id=${encodeURIComponent(agentId)}&user_key=${encodeURIComponent(userKey)}`,
    ),
  listSchedules: (
    agentId: string,
    params?: { status?: string; user_key?: string; limit?: number },
  ) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.user_key) q.set('user_key', params.user_key)
    if (params?.limit) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request<Schedule[]>(
      `/api/agents/${agentId}/schedules${qs ? `?${qs}` : ''}`,
    )
  },
  scheduleStats: (agentId: string, userKey?: string) => {
    const q = new URLSearchParams()
    if (userKey) q.set('user_key', userKey)
    const qs = q.toString()
    return request<ScheduleStats>(
      `/api/agents/${agentId}/schedules/stats${qs ? `?${qs}` : ''}`,
    )
  },
  pauseSchedule: (jobId: string) =>
    request<Schedule>(`/api/schedules/${jobId}/pause`, { method: 'POST' }),
  resumeSchedule: (jobId: string) =>
    request<Schedule>(`/api/schedules/${jobId}/resume`, { method: 'POST' }),
  cancelSchedule: (jobId: string) =>
    request<Schedule>(`/api/schedules/${jobId}/cancel`, { method: 'POST' }),
  deleteSchedule: (jobId: string) =>
    request<void>(`/api/schedules/${jobId}`, { method: 'DELETE' }),
  listModels: () => request<ModelProfile[]>('/api/models'),
  listModelPresets: () => request<ModelPreset[]>('/api/models/presets'),
  discoverModels: (body: { base_url: string; api_key?: string; profile_id?: string }) =>
    request<DiscoverModelsResult>('/api/models/discover', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  createModel: (body: unknown) =>
    request<ModelProfile>('/api/models', { method: 'POST', body: JSON.stringify(body) }),
  updateModel: (id: string, body: unknown) =>
    request<ModelProfile>(`/api/models/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteModel: (id: string) =>
    request<void>(`/api/models/${id}`, { method: 'DELETE' }),
  testModel: (id: string) =>
    request<{ status: string; message: string }>(`/api/models/${id}/test`, { method: 'POST' }),
  usageSummary: (params?: {
    group_by?: 'day' | 'model' | 'agent'
    from_time?: string
    to_time?: string
    agent_id?: string
  }) => {
    const q = new URLSearchParams()
    if (params?.group_by) q.set('group_by', params.group_by)
    if (params?.from_time) q.set('from_time', params.from_time)
    if (params?.to_time) q.set('to_time', params.to_time)
    if (params?.agent_id) q.set('agent_id', params.agent_id)
    const qs = q.toString()
    return request<UsageSummary>(`/api/usage/summary${qs ? `?${qs}` : ''}`)
  },
  listUsage: (params?: {
    session_id?: string
    agent_id?: string
    model_profile_id?: string
    page?: number
    pageSize?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.session_id) q.set('session_id', params.session_id)
    if (params?.agent_id) q.set('agent_id', params.agent_id)
    if (params?.model_profile_id) q.set('model_profile_id', params.model_profile_id)
    if (params?.page) q.set('page', String(params.page))
    if (params?.pageSize) q.set('page_size', String(params.pageSize))
    const qs = q.toString()
    return request<LlmUsagePage>(`/api/usage${qs ? `?${qs}` : ''}`)
  },
  listTeams: () => request<AgentTeam[]>('/api/teams'),
  getTeam: (id: string) => request<AgentTeam>(`/api/teams/${id}`),
  createTeam: (body: unknown) =>
    request<AgentTeam>('/api/teams', { method: 'POST', body: JSON.stringify(body) }),
  updateTeam: (id: string, body: unknown) =>
    request<AgentTeam>(`/api/teams/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteTeam: (id: string) =>
    request<void>(`/api/teams/${id}`, { method: 'DELETE' }),
  getSessionBoard: (sessionId: string, create = false) =>
    request<BoardProject>(
      `/api/sessions/${sessionId}/board${create ? '?create=true' : ''}`,
    ),
  writeSessionBoardArtifact: (
    sessionId: string,
    body: { path: string; title?: string; content: string; author_agent_id?: string },
  ) =>
    request<BoardArtifact>(`/api/sessions/${sessionId}/board/artifacts`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  listRunChildren: (runId: string) =>
    request<ChildRun[]>(`/api/runs/${runId}/children`),
  listSessionDelegations: (sessionId: string) =>
    request<ChildRun[]>(`/api/sessions/${sessionId}/delegations`),
}
