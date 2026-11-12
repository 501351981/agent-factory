import type { SessionTimelineDto } from './types'

export type MessageDto = {
  id: string
  session_id: string
  role: string
  content?: string | null
  tool_call_id?: string | null
  tool_calls?: unknown[] | null
  created_at: string
}

function authHeaders(apiKey?: string): HeadersInit {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (!apiKey) return headers
  // Embed JWT (header.payload.sig) → Bearer; otherwise treat as platform X-API-Key.
  if (apiKey.split('.').length === 3) {
    headers.Authorization = `Bearer ${apiKey}`
  } else {
    headers['X-API-Key'] = apiKey
  }
  return headers
}

export type ModelProfileDto = {
  id: string
  name: string
  provider: string
  model_name: string
  enabled: boolean
  is_default: boolean
  context_window_tokens?: number
  reserve_output_tokens?: number
}

export type SkillDto = {
  name: string
  description?: string
}

export type AgentDto = {
  id: string
  name: string
  model_profile_id?: string | null
  model_profile_name?: string | null
  skills?: SkillDto[]
  logo_url?: string | null
  welcome_title?: string | null
  welcome_subtitle?: string | null
  suggested_prompts?: string[]
}

export async function fetchModels(
  baseUrl: string,
  apiKey?: string,
): Promise<ModelProfileDto[]> {
  const resp = await fetch(`${baseUrl}/api/models`, {
    headers: authHeaders(apiKey),
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  return resp.json()
}

export async function fetchAgent(
  baseUrl: string,
  agentId: string,
  apiKey?: string,
): Promise<AgentDto> {
  const resp = await fetch(`${baseUrl}/api/agents/${agentId}`, {
    headers: authHeaders(apiKey),
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  return resp.json()
}

export async function createSession(
  baseUrl: string,
  agentId: string,
  apiKey?: string,
  userKey?: string,
  promptVars?: Record<string, string>,
  locale?: string,
  clientDeviceId?: string,
): Promise<{ id: string; client_device_id?: string | null }> {
  const resp = await fetch(`${baseUrl}/api/sessions`, {
    method: 'POST',
    headers: authHeaders(apiKey),
    body: JSON.stringify({
      agent_id: agentId,
      user_key: userKey,
      locale: locale || 'zh',
      prompt_vars: promptVars || {},
      client_device_id: clientDeviceId || undefined,
    }),
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  return resp.json()
}

export async function fetchMessages(
  baseUrl: string,
  sessionId: string,
  apiKey?: string,
): Promise<MessageDto[]> {
  const resp = await fetch(`${baseUrl}/api/sessions/${sessionId}/messages`, {
    headers: authHeaders(apiKey),
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  return resp.json()
}

export async function fetchTimeline(
  baseUrl: string,
  sessionId: string,
  apiKey?: string,
): Promise<SessionTimelineDto> {
  const resp = await fetch(`${baseUrl}/api/sessions/${sessionId}/timeline`, {
    headers: authHeaders(apiKey),
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  return resp.json()
}

export type QueueItemDto = {
  id: string
  session_id: string
  seq: number
  message: string
  model_profile_id?: string | null
  status: string
  run_id?: string | null
  created_at: string
  updated_at: string
}

export async function fetchQueue(
  baseUrl: string,
  sessionId: string,
  apiKey?: string,
): Promise<QueueItemDto[]> {
  const resp = await fetch(`${baseUrl}/api/sessions/${sessionId}/queue`, {
    headers: authHeaders(apiKey),
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  const data = (await resp.json()) as { items?: QueueItemDto[] }
  return data.items || []
}

export async function cancelQueueItem(
  baseUrl: string,
  sessionId: string,
  itemId: string,
  apiKey?: string,
): Promise<void> {
  const resp = await fetch(`${baseUrl}/api/sessions/${sessionId}/queue/${itemId}`, {
    method: 'DELETE',
    headers: authHeaders(apiKey),
  })
  if (!resp.ok && resp.status !== 404) {
    throw new Error(await resp.text())
  }
}

export async function cancelRun(
  baseUrl: string,
  runId: string,
  apiKey?: string,
): Promise<void> {
  const resp = await fetch(`${baseUrl}/api/runs/${runId}/cancel`, {
    method: 'POST',
    headers: authHeaders(apiKey),
  })
  if (!resp.ok && resp.status !== 404) {
    throw new Error(await resp.text())
  }
}

export type StreamHandlers = {
  onEvent: (event: Record<string, unknown>) => void
  onError?: (error: Error) => void
}

async function readSseStream(
  resp: Response,
  handlers: StreamHandlers,
): Promise<void> {
  if (!resp.body) {
    throw new Error('missing response body')
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() || ''
    for (const chunk of chunks) {
      const line = chunk
        .split('\n')
        .map((l) => l.trim())
        .find((l) => l.startsWith('data:'))
      if (!line) continue
      const raw = line.slice(5).trim()
      if (!raw) continue
      try {
        handlers.onEvent(JSON.parse(raw) as Record<string, unknown>)
      } catch (err) {
        handlers.onError?.(err as Error)
      }
    }
  }
}

export async function streamChat(
  baseUrl: string,
  body: {
    agent_id: string
    message: string
    session_id?: string | null
    user_key?: string | null
    locale?: string
    prompt_vars?: Record<string, string>
    model_profile_id?: string | null
  },
  apiKey: string | undefined,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${baseUrl}/api/chat`, {
    method: 'POST',
    headers: authHeaders(apiKey),
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  await readSseStream(resp, handlers)
}

/** Replay + live subscribe for an existing run (page refresh reconnect). */
export async function streamRunEvents(
  baseUrl: string,
  runId: string,
  apiKey: string | undefined,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  after = 0,
): Promise<void> {
  const resp = await fetch(`${baseUrl}/api/runs/${runId}/events?after=${after}`, {
    method: 'GET',
    headers: authHeaders(apiKey),
    signal,
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  await readSseStream(resp, handlers)
}

/** Resume a run paused on ask_user or tool approval (HITL). */
export async function streamResumeRun(
  baseUrl: string,
  runId: string,
  body: {
    call_id: string
    answer?: string | null
    selected_option?: string | null
    decision?: 'approve' | 'edit' | 'reject' | null
    edited_arguments?: Record<string, unknown> | null
  },
  apiKey: string | undefined,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${baseUrl}/api/runs/${runId}/resume`, {
    method: 'POST',
    headers: authHeaders(apiKey),
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok) {
    throw new Error(await resp.text())
  }
  await readSseStream(resp, handlers)
}
