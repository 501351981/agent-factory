import type { ChatTurn, HumanAsk, HumanApproval, TimelineRunDto, ToolStep } from './types'

function answeredCallIds(run: TimelineRunDto): Set<string> {
  return new Set(
    (run.steps || [])
      .filter((s) => s.type === 'tool_result' && s.call_id)
      .map((s) => s.call_id as string),
  )
}

function extractHumanAsk(run: TimelineRunDto): HumanAsk | undefined {
  if (run.status !== 'waiting_human') return undefined
  const answered = answeredCallIds(run)
  const pending = (run.steps || []).find(
    (s) => s.type === 'tool_call' && s.name === 'ask_user' && s.call_id && !answered.has(s.call_id),
  )
  if (!pending?.call_id) return undefined
  const args = (pending.payload?.arguments || {}) as Record<string, unknown>
  const options = Array.isArray(args.options)
    ? args.options.map((x) => String(x).trim()).filter(Boolean)
    : []
  const allowFree =
    args.allow_free_text === undefined ? true : Boolean(args.allow_free_text)
  return {
    callId: pending.call_id,
    question: String(args.question || '请提供更多信息以继续。'),
    options,
    allowFreeText: allowFree || options.length === 0,
    expiresAt: run.waiting_expires_at || null,
    draft: '',
  }
}

function extractHumanApproval(run: TimelineRunDto): HumanApproval | undefined {
  if (run.status !== 'waiting_human') return undefined
  const answered = answeredCallIds(run)
  const pending = (run.steps || []).find((s) => {
    if (s.type !== 'tool_call' || !s.call_id || answered.has(s.call_id)) return false
    if (s.name === 'ask_user') return false
    return s.payload?.hitl === 'approval'
  })
  if (!pending?.call_id) return undefined
  const args = pending.payload?.arguments
  return {
    callId: pending.call_id,
    name: pending.name || 'tool',
    arguments: args ?? {},
    expiresAt: run.waiting_expires_at || null,
    editedArgumentsJson: JSON.stringify(args ?? {}, null, 2),
  }
}

/** Convert persisted session timeline runs into chat UI turns. */
export function timelineToTurns(runs: TimelineRunDto[]): ChatTurn[] {
  return runs.map((run) => {
    const tools: ToolStep[] = []
    let reasoning = ''
    let assistantContent = ''
    for (const step of run.steps || []) {
      if (step.type === 'reasoning') {
        reasoning += step.content || ''
      } else if (step.type === 'tool_call') {
        const pendingHitl =
          run.status === 'waiting_human' &&
          (step.name === 'ask_user' || step.payload?.hitl === 'approval')
        tools.push({
          callId: step.call_id || step.id,
          name: step.name || 'tool',
          arguments: step.payload?.arguments,
          status: pendingHitl ? 'running' : 'done',
        })
      } else if (step.type === 'tool_result') {
        const target = tools.find((t) => t.callId === step.call_id) || tools[tools.length - 1]
        if (target) {
          try {
            target.result = step.content ? JSON.parse(step.content) : step.payload
          } catch {
            target.result = step.content
          }
          target.status = 'done'
        }
      } else if (step.type === 'assistant_message') {
        const hasTools =
          Array.isArray(step.payload?.tool_calls) &&
          (step.payload?.tool_calls as unknown[]).length
        if (!hasTools && step.content) {
          assistantContent = step.content
        } else if (!assistantContent && step.content) {
          assistantContent = step.content
        }
      }
    }
    const status = ([
      'running',
      'waiting_human',
      'completed',
      'failed',
      'cancelled',
      'limit_reached',
    ].includes(run.status)
      ? run.status
      : 'completed') as ChatTurn['status']
    const humanAsk = extractHumanAsk(run)
    const humanApproval = extractHumanApproval(run)
    if (humanAsk) {
      const askTool = tools.find((t) => t.callId === humanAsk.callId)
      if (askTool) askTool.status = 'running'
    }
    if (humanApproval) {
      const approvalTool = tools.find((t) => t.callId === humanApproval.callId)
      if (approvalTool) approvalTool.status = 'running'
    }
    return {
      id: run.run_id,
      runId: run.run_id,
      status,
      userMessage: run.user_message,
      reasoning,
      tools,
      assistantContent,
      processExpanded: status === 'running' || status === 'waiting_human',
      error: run.error_message || undefined,
      humanAsk,
      humanApproval,
    }
  })
}
