<script setup lang="ts">
import { Service } from '@element-plus/icons-vue'
import MarkdownView from './MarkdownView.vue'
import ProcessPanel from './ProcessPanel.vue'
import type { ChatTurn } from './types'

defineProps<{
  turn: ChatTurn
}>()

const emit = defineEmits<{
  (e: 'toggle-process'): void
  (e: 'submit-answer', option?: string): void
  (e: 'submit-approval', decision: 'approve' | 'edit' | 'reject'): void
}>()

function isLiveGenerating(turn: ChatTurn): boolean {
  return turn.status === 'running'
}

function isWaitingHuman(turn: ChatTurn): boolean {
  return (
    turn.status === 'waiting_human' && (!!turn.humanAsk || !!turn.humanApproval)
  )
}

function statusKind(turn: ChatTurn): 'running' | 'waiting' | 'cancelled' | 'failed' | null {
  if (turn.status === 'completed') return null
  if (isLiveGenerating(turn)) return 'running'
  if (isWaitingHuman(turn)) return 'waiting'
  if (turn.status === 'cancelled') return 'cancelled'
  return 'failed'
}

function statusLabel(turn: ChatTurn): string {
  const kind = statusKind(turn)
  if (kind === 'running') return '进行中'
  if (kind === 'waiting') {
    return turn.humanApproval ? '等待审批' : '等待回答'
  }
  if (kind === 'cancelled') return '已停止'
  if (turn.status === 'limit_reached') return '轮次上限'
  return '失败'
}

function formatArgs(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}

function hasProcess(turn: ChatTurn): boolean {
  return !!turn.reasoning.trim() || turn.tools.length > 0
}
</script>

<template>
  <div class="af-turn">
    <div class="af-msg af-msg--user">
      <div class="af-msg__bubble">
        <pre class="af-msg__bubble--plain">{{ turn.userMessage }}</pre>
      </div>
    </div>

    <div class="af-msg af-msg--assistant">
      <div class="af-msg__avatar" aria-hidden="true">
        <el-icon><Service /></el-icon>
      </div>
      <div class="af-msg__body">
        <span
          v-if="statusKind(turn) && statusKind(turn) !== 'running'"
          class="af-msg__status"
          :class="`af-msg__status--${statusKind(turn)}`"
        >
          <span class="af-msg__status-dot" aria-hidden="true" />
          {{ statusLabel(turn) }}
        </span>

        <ProcessPanel
          v-if="hasProcess(turn)"
          :turn="turn"
          @toggle="emit('toggle-process')"
        />

        <div v-if="turn.humanAsk && turn.status === 'waiting_human'" class="af-ask">
          <div class="af-ask__label">需要你的确认</div>
          <div class="af-ask__question">{{ turn.humanAsk.question }}</div>
          <div v-if="turn.humanAsk.options.length" class="af-ask__options">
            <el-button
              v-for="opt in turn.humanAsk.options"
              :key="opt"
              size="small"
              round
              :disabled="!!turn.humanAsk.submitting"
              @click="emit('submit-answer', opt)"
            >
              {{ opt }}
            </el-button>
          </div>
          <div v-if="turn.humanAsk.allowFreeText" class="af-ask__free">
            <el-input
              v-model="turn.humanAsk.draft"
              type="textarea"
              :rows="2"
              placeholder="输入回答…"
              :disabled="!!turn.humanAsk.submitting"
              @keydown.enter.exact.prevent="emit('submit-answer')"
            />
            <el-button
              type="primary"
              size="small"
              round
              :loading="!!turn.humanAsk.submitting"
              style="margin-top: 8px"
              @click="emit('submit-answer')"
            >
              提交回答
            </el-button>
          </div>
        </div>

        <div
          v-if="turn.humanApproval && turn.status === 'waiting_human'"
          class="af-ask af-ask--approval"
        >
          <div class="af-ask__label">工具待审批</div>
          <div class="af-ask__question">
            允许执行 <code>{{ turn.humanApproval.name }}</code> ？
          </div>
          <pre class="af-ask__args">{{ formatArgs(turn.humanApproval.arguments) }}</pre>
          <div v-if="turn.humanApproval.editMode" class="af-ask__free">
            <el-input
              v-model="turn.humanApproval.editedArgumentsJson"
              type="textarea"
              :rows="5"
              placeholder='编辑参数 JSON，例如 {"path":"."}'
              :disabled="!!turn.humanApproval.submitting"
            />
          </div>
          <div class="af-ask__options" style="margin-top: 10px">
            <el-button
              type="primary"
              size="small"
              round
              :loading="!!turn.humanApproval.submitting && !turn.humanApproval.editMode"
              :disabled="!!turn.humanApproval.submitting"
              @click="emit('submit-approval', 'approve')"
            >
              批准
            </el-button>
            <el-button
              size="small"
              round
              :disabled="!!turn.humanApproval.submitting"
              @click="turn.humanApproval.editMode = !turn.humanApproval.editMode"
            >
              {{ turn.humanApproval.editMode ? '取消修改' : '修改参数' }}
            </el-button>
            <el-button
              v-if="turn.humanApproval.editMode"
              type="warning"
              size="small"
              round
              :loading="!!turn.humanApproval.submitting"
              :disabled="!!turn.humanApproval.submitting"
              @click="emit('submit-approval', 'edit')"
            >
              用修改后的参数执行
            </el-button>
            <el-button
              type="danger"
              size="small"
              round
              plain
              :disabled="!!turn.humanApproval.submitting"
              @click="emit('submit-approval', 'reject')"
            >
              拒绝
            </el-button>
          </div>
        </div>

        <div
          v-if="turn.assistantContent || isLiveGenerating(turn)"
          class="af-msg__bubble"
          :class="{
            'af-msg__bubble--typing': isLiveGenerating(turn) && !turn.assistantContent,
          }"
        >
          <template v-if="turn.assistantContent">
            <MarkdownView :content="turn.assistantContent" />
            <span v-if="isLiveGenerating(turn)" class="af-msg__cursor" aria-hidden="true" />
          </template>
          <span v-else class="af-msg__typing" aria-label="正在生成">
            <span /><span /><span />
          </span>
        </div>
        <div
          v-else-if="turn.status === 'cancelled' && !turn.assistantContent"
          class="af-msg__bubble af-msg__bubble--typing"
        >
          已取消，未生成回复
        </div>
        <div v-if="turn.error" class="af-turn__error">{{ turn.error }}</div>
      </div>
    </div>
  </div>
</template>
