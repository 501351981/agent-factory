<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowRight } from '@element-plus/icons-vue'
import HitlCard from './HitlCard.vue'
import MarkdownView from './MarkdownView.vue'
import ProcessPanel from './ProcessPanel.vue'
import type { ChatTurn } from './types'

const props = withDefaults(
  defineProps<{
    turn: ChatTurn
    /**
     * Where HITL cards render:
     * - `inline` (default): inside the turn (admin / history viewers)
     * - `dock`: only a status chip here; parent docks the real card at the bottom
     */
    hitlPlacement?: 'inline' | 'dock'
  }>(),
  { hitlPlacement: 'inline' },
)

const emit = defineEmits<{
  (e: 'toggle-process'): void
  (e: 'submit-answer', option?: string): void
  (
    e: 'submit-approval',
    decision: 'approve' | 'edit' | 'reject',
    opts?: { grantRun?: boolean },
  ): void
}>()

/** Finished turns keep interims collapsed; in-progress keep them open as separate blocks. */
const interimExpanded = ref(false)

const interimList = computed(() =>
  (props.turn.interimMessages || []).map((t) => t.trim()).filter(Boolean),
)

const showInterimOpen = computed(() => {
  if (!interimList.value.length) return false
  if (props.turn.status === 'running' || props.turn.status === 'waiting_human') return true
  return interimExpanded.value
})

const showInlineHitl = computed(
  () =>
    props.hitlPlacement === 'inline' &&
    props.turn.status === 'waiting_human' &&
    (!!props.turn.humanAsk || !!props.turn.humanApproval),
)

watch(
  () => props.turn.status,
  (status) => {
    if (status === 'running' || status === 'waiting_human') {
      interimExpanded.value = true
    } else {
      interimExpanded.value = false
    }
  },
  { immediate: true },
)

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
    if (props.hitlPlacement === 'dock') {
      return turn.humanApproval ? '等待审批（见底部）' : '等待回答（见底部）'
    }
    return turn.humanApproval ? '等待审批' : '等待回答'
  }
  if (kind === 'cancelled') return '已停止'
  if (turn.status === 'limit_reached') return '已达上限'
  return '失败'
}

function hasProcess(turn: ChatTurn): boolean {
  return !!turn.reasoning.trim() || turn.tools.length > 0
}
</script>

<template>
  <div class="af-turn">
    <div v-if="turn.userMessage" class="af-msg af-msg--user">
      <div class="af-msg__bubble">
        <pre class="af-msg__bubble--plain">{{ turn.userMessage }}</pre>
      </div>
    </div>

    <div class="af-msg af-msg--assistant">
      <div class="af-msg__body">
        <span v-if="turn.fromSchedule" class="af-msg__status af-msg__status--schedule">
          定时
        </span>
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

        <HitlCard
          v-if="showInlineHitl"
          :turn="turn"
          @submit-answer="(opt) => emit('submit-answer', opt)"
          @submit-approval="(d, opts) => emit('submit-approval', d, opts)"
        />

        <div v-if="interimList.length" class="af-interim">
          <button
            v-if="turn.status !== 'running' && turn.status !== 'waiting_human'"
            type="button"
            class="af-interim__toggle"
            @click="interimExpanded = !interimExpanded"
          >
            <el-icon class="af-interim__chevron">
              <ArrowDown v-if="showInterimOpen" />
              <ArrowRight v-else />
            </el-icon>
            <span>中间回复 · {{ interimList.length }} 条</span>
          </button>
          <div v-if="showInterimOpen" class="af-interim__list">
            <div
              v-for="(text, idx) in interimList"
              :key="`interim-${idx}`"
              class="af-msg__bubble af-msg__bubble--interim"
            >
              <MarkdownView :content="text" />
            </div>
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
          v-else-if="turn.status === 'cancelled' && !turn.assistantContent && !interimList.length"
          class="af-msg__bubble af-msg__bubble--typing"
        >
          已取消，未生成回复
        </div>
        <div v-if="turn.error" class="af-turn__error">{{ turn.error }}</div>
      </div>
    </div>
  </div>
</template>
