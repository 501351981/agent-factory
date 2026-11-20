import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { timelineToTurns } from './timeline.ts'
import type { TimelineRunDto } from './types.ts'

describe('timelineToTurns interim messages', () => {
  it('splits tool-bearing assistant texts into interimMessages and keeps final', () => {
    const run: TimelineRunDto = {
      run_id: 'r1',
      status: 'completed',
      user_message: 'scan',
      rounds_used: 3,
      started_at: '2026-01-01T00:00:00Z',
      steps: [
        {
          id: '1',
          type: 'assistant_message',
          content: '先看目录结构',
          payload: { tool_calls: [{ id: 'c1' }] },
          created_at: '2026-01-01T00:00:01Z',
        },
        {
          id: '2',
          type: 'tool_call',
          call_id: 'c1',
          name: 'shell',
          created_at: '2026-01-01T00:00:02Z',
        },
        {
          id: '3',
          type: 'tool_result',
          call_id: 'c1',
          content: '{"ok":true}',
          created_at: '2026-01-01T00:00:03Z',
        },
        {
          id: '4',
          type: 'assistant_message',
          content: '再扫一层',
          payload: { tool_calls: [{ id: 'c2' }] },
          created_at: '2026-01-01T00:00:04Z',
        },
        {
          id: '5',
          type: 'tool_call',
          call_id: 'c2',
          name: 'shell',
          created_at: '2026-01-01T00:00:05Z',
        },
        {
          id: '6',
          type: 'tool_result',
          call_id: 'c2',
          content: '{"ok":true}',
          created_at: '2026-01-01T00:00:06Z',
        },
        {
          id: '7',
          type: 'assistant_message',
          content: '扫描完成，共 49 个目录。',
          created_at: '2026-01-01T00:00:07Z',
        },
      ],
    }

    const [turn] = timelineToTurns([run])
    assert.deepEqual(turn.interimMessages, ['先看目录结构', '再扫一层'])
    assert.equal(turn.assistantContent, '扫描完成，共 49 个目录。')
  })

  it('keeps only interim batches while run is still going', () => {
    const run: TimelineRunDto = {
      run_id: 'r2',
      status: 'running',
      user_message: 'scan',
      rounds_used: 1,
      started_at: '2026-01-01T00:00:00Z',
      steps: [
        {
          id: '1',
          type: 'assistant_message',
          content: '第一层并行扫',
          payload: { tool_calls: [{ id: 'c1' }] },
          created_at: '2026-01-01T00:00:01Z',
        },
        {
          id: '2',
          type: 'tool_call',
          call_id: 'c1',
          name: 'shell',
          created_at: '2026-01-01T00:00:02Z',
        },
      ],
    }

    const [turn] = timelineToTurns([run])
    assert.deepEqual(turn.interimMessages, ['第一层并行扫'])
    assert.equal(turn.assistantContent, '')
    assert.equal(turn.status, 'running')
  })
})
