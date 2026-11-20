<script setup lang="ts">
/**
 * HITL ask / tool-approval card. Rendered inline in a turn or docked above the composer.
 */
import { computed } from 'vue'
import type { ChatTurn } from './types'

const props = defineProps<{
  turn: ChatTurn
}>()

const emit = defineEmits<{
  (e: 'submit-answer', option?: string): void
  (
    e: 'submit-approval',
    decision: 'approve' | 'edit' | 'reject',
    opts?: { grantRun?: boolean },
  ): void
}>()

const ask = computed(() =>
  props.turn.status === 'waiting_human' ? props.turn.humanAsk : undefined,
)
const approval = computed(() =>
  props.turn.status === 'waiting_human' ? props.turn.humanApproval : undefined,
)

function formatArgs(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}

/** Hide reason-like keys already shown above the JSON block. */
function approvalArgsForDisplay(value: unknown): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value
  const hide = new Set(['reason', 'purpose', 'summary', 'intent'])
  const next: Record<string, unknown> = {}
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    if (hide.has(key)) continue
    next[key] = entry
  }
  return next
}
</script>

<template>
  <div v-if="ask" class="af-ask">
    <div class="af-ask__label">需要你的确认</div>
    <div class="af-ask__question">{{ ask.question }}</div>
    <div v-if="ask.options.length" class="af-ask__options">
      <el-button
        v-for="opt in ask.options"
        :key="opt"
        size="small"
        round
        :disabled="!!ask.submitting"
        @click="emit('submit-answer', opt)"
      >
        {{ opt }}
      </el-button>
    </div>
    <div v-if="ask.allowFreeText" class="af-ask__free">
      <el-input
        v-model="ask.draft"
        type="textarea"
        :rows="2"
        placeholder="输入回答…"
        :disabled="!!ask.submitting"
        @keydown.enter.exact.prevent="emit('submit-answer')"
      />
      <el-button
        type="primary"
        size="small"
        round
        :loading="!!ask.submitting"
        style="margin-top: 8px"
        @click="emit('submit-answer')"
      >
        提交回答
      </el-button>
    </div>
  </div>

  <div v-else-if="approval" class="af-ask af-ask--approval">
    <div class="af-ask__label">工具待审批</div>
    <div class="af-ask__question">
      允许执行 <code>{{ approval.name }}</code> ？
    </div>
    <div v-if="approval.reason" class="af-ask__reason">
      {{ approval.reason }}
    </div>
    <div v-else-if="approval.toolDescription" class="af-ask__tool-desc">
      {{ approval.toolDescription }}
    </div>
    <pre class="af-ask__args">{{
      formatArgs(approvalArgsForDisplay(approval.arguments))
    }}</pre>
    <div v-if="approval.editMode" class="af-ask__free">
      <el-input
        v-model="approval.editedArgumentsJson"
        type="textarea"
        :rows="5"
        placeholder='编辑参数 JSON，例如 {"path":"."}'
        :disabled="!!approval.submitting"
      />
    </div>
    <div class="af-ask__options" style="margin-top: 10px">
      <el-button
        type="primary"
        size="small"
        round
        :loading="!!approval.submitting && !approval.editMode"
        :disabled="!!approval.submitting"
        @click="emit('submit-approval', 'approve')"
      >
        批准本次
      </el-button>
      <el-button
        type="success"
        size="small"
        round
        :loading="!!approval.submitting && !approval.editMode"
        :disabled="!!approval.submitting || !!approval.editMode"
        @click="emit('submit-approval', 'approve', { grantRun: true })"
      >
        本轮授权执行
      </el-button>
      <el-button
        size="small"
        round
        :disabled="!!approval.submitting"
        @click="approval.editMode = !approval.editMode"
      >
        {{ approval.editMode ? '取消修改' : '修改参数' }}
      </el-button>
      <el-button
        v-if="approval.editMode"
        type="warning"
        size="small"
        round
        :loading="!!approval.submitting"
        :disabled="!!approval.submitting"
        @click="emit('submit-approval', 'edit')"
      >
        用修改后的参数执行
      </el-button>
      <el-button
        type="danger"
        size="small"
        round
        plain
        :disabled="!!approval.submitting"
        @click="emit('submit-approval', 'reject')"
      >
        拒绝
      </el-button>
    </div>
    <div class="af-ask__hint">
      「本轮授权执行」：批准当前工具，并跳过本条用户消息内后续工具审批（下一条消息仍会重新询问）。
    </div>
  </div>
</template>
