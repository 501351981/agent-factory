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
  /** Per-call why-this-invocation text from tool args (reason/purpose/…). */
  reason?: string | null
  /** Static ToolDef.description fallback. */
  toolDescription?: string | null
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
  /**
   * Earlier assistant texts from tool-bearing model rounds (progress updates).
   * While running they stay visible as separate blocks; when the turn finishes
   * they collapse under「中间回复」and only `assistantContent` stays prominent.
   */
  interimMessages?: string[]
  /** process panel expanded; running defaults true, finished defaults false */
  processExpanded: boolean
  /** Fired by the agent scheduler (assistant_message or synthetic_user). */
  fromSchedule?: boolean
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
  parent_run_id?: string | null
  root_session_id?: string | null
  delegation_call_id?: string | null
  delegation_depth?: number
  run_id: string
  status: string
  user_message: string
  model_name?: string | null
  rounds_used: number
  finish_reason?: string | null
  error_message?: string | null
  from_schedule?: boolean
  started_at: string
  finished_at?: string | null
  waiting_expires_at?: string | null
  steps: TimelineStepDto[]
}

export type SessionTimelineDto = {
  session_id: string
  runs: TimelineRunDto[]
}
