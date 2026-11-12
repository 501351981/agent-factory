import { API_BASE, API_KEY } from '../env'

function headers(): HeadersInit {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (API_KEY) h['X-API-Key'] = API_KEY
  return h
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers(), ...(init?.headers || {}) },
  })
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

export type AgentRuntime = {
  max_tool_rounds?: number
  on_max_rounds?: 'summarize' | 'stop'
  tool_timeout_seconds?: number
  run_timeout_seconds?: number
  parallel_tool_calls?: boolean
  tool_result_max_chars?: number
  memory?: MemoryPolicy
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
  access_key?: string | null
  has_secret_key?: boolean
  created_at: string
  updated_at: string
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

export const api = {
  listAgents: () => request<Agent[]>('/api/agents'),
  createAgent: (body: unknown) =>
    request<Agent>('/api/agents', { method: 'POST', body: JSON.stringify(body) }),
  updateAgent: (id: string, body: unknown) =>
    request<Agent>(`/api/agents/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteAgent: (id: string) =>
    request<void>(`/api/agents/${id}`, { method: 'DELETE' }),
  uploadLogo: async (file: File) => {
    const body = new FormData()
    body.append('file', file)
    const headers: HeadersInit = {}
    if (API_KEY) headers['X-API-Key'] = API_KEY
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
  listSessions: (params?: { agentId?: string; page?: number; pageSize?: number }) => {
    const q = new URLSearchParams()
    if (params?.agentId) q.set('agent_id', params.agentId)
    if (params?.page) q.set('page', String(params.page))
    if (params?.pageSize) q.set('page_size', String(params.pageSize))
    const qs = q.toString()
    return request<SessionPage>(`/api/sessions${qs ? `?${qs}` : ''}`)
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
}
