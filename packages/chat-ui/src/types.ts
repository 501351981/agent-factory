export type ChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool' | 'event'
  content: string
  toolName?: string
}

export type ToolStep = {
  callId: string
  name: string
  arguments?: unknown
  result?: unknown
  status: 'running' | 'done'
}

export type HumanAsk = {
  callId: string
  question: string
  options: string[]
  allowFreeText: boolean
  expiresAt?: string | null
  submitting?: boolean
  draft?: string
}

export type HumanApproval = {
  callId: string
  name: string
  arguments: unknown
  expiresAt?: string | null
  submitting?: boolean
  editMode?: boolean
  editedArgumentsJson?: string
}

export type ChatTurn = {
  id: string
  runId?: string
  status: 'running' | 'waiting_human' | 'completed' | 'failed' | 'cancelled' | 'limit_reached'
  userMessage: string
  reasoning: string
  tools: ToolStep[]
  assistantContent: string
  /** process panel expanded; running defaults true, finished defaults false */
  processExpanded: boolean
  error?: string
  humanAsk?: HumanAsk
  humanApproval?: HumanApproval
}

export type AgentChatProps = {
  baseUrl: string
  agentId: string
  sessionId?: string
  apiKey?: string
  /** Stable business user id for long-term memory scope. */
  userKey?: string
  /** Session language: zh | en | ja. */
  locale?: string
  /** Variables for Agent system_prompt template, e.g. { userId: 'u_1' }. */
  promptVars?: Record<string, string>
  /** Local Bridge device_id bound to this session for client tools. */
  clientDeviceId?: string
  /** Show top bar (agent name / session). Embed hosts usually hide this. Default true. */
  showHeader?: boolean
  /**
   * Who creates sessions when none is bound:
   * - `auto`: chat-ui calls POST /api/sessions (standalone / simple demos)
   * - `host`: ask parent via postMessage `session_create_request`; wait for `set_session`
   */
  sessionCreateMode?: 'auto' | 'host'
}

export type TimelineStepDto = {
  id: string
  type: string
  call_id?: string | null
  name?: string | null
  content?: string | null
  payload?: Record<string, unknown> | null
  created_at: string
}

export type TimelineRunDto = {
  run_id: string
  status: string
  user_message: string
  model_name?: string | null
  rounds_used: number
  finish_reason?: string | null
  error_message?: string | null
  started_at: string
  finished_at?: string | null
  waiting_expires_at?: string | null
  steps: TimelineStepDto[]
}

export type SessionTimelineDto = {
  session_id: string
  runs: TimelineRunDto[]
}
