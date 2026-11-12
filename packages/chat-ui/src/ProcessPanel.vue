<script setup lang="ts">
/**
 * 思考 / 工具调用：紧凑列表。
 * - 整块：执行中默认展开，结束后默认收起
 * - 单步：点击行展开/收起详情；进行中的步骤默认展开
 * - 详情完全撑开，不做限高/内部滚动（交给聊天列表滚动）
 */
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowRight, Tools } from '@element-plus/icons-vue'
import MarkdownView from './MarkdownView.vue'
import type { ChatTurn, ToolStep } from './types'

type ProcessStep =
  | { id: string; type: 'thinking'; status: 'running' | 'done' }
  | { id: string; type: 'tool'; tool: ToolStep }

const props = defineProps<{
  turn: ChatTurn
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
}>()

/** 用户手动展开的步骤详情（思考正文 / 工具参数结果） */
const openDetailIds = ref<Set<string>>(new Set())

const isInProgress = computed(
  () => props.turn.status === 'running' || props.turn.status === 'waiting_human',
)

const steps = computed((): ProcessStep[] => {
  const list: ProcessStep[] = []
  if (props.turn.reasoning.trim()) {
    const thinkingDone =
      !!props.turn.assistantContent ||
      props.turn.tools.some((t) => t.status === 'running' || t.status === 'done') ||
      !isInProgress.value
    list.push({
      id: 'thinking',
      type: 'thinking',
      status: thinkingDone ? 'done' : 'running',
    })
  }
  for (const tool of props.turn.tools) {
    list.push({ id: `tool:${tool.callId}`, type: 'tool', tool })
  }
  return list
})

const headerLabel = computed(() => {
  if (isInProgress.value) return '任务进行中'
  const parts: string[] = ['任务完成']
  if (props.turn.reasoning.trim()) parts.push('含思考')
  if (props.turn.tools.length) parts.push(`${props.turn.tools.length} 个工具`)
  return parts.join(' · ')
})

/** 进行中默认展开；用户可点行强制收起；完成后靠 openDetailIds */
function isDetailOpen(step: ProcessStep): boolean {
  if (openDetailIds.value.has(`collapse:${step.id}`)) return false
  if (isStepRunning(step)) return true
  return openDetailIds.value.has(step.id)
}

function toggleDetail(step: ProcessStep): void {
  const next = new Set(openDetailIds.value)
  if (isDetailOpen(step)) {
    next.delete(step.id)
    if (isStepRunning(step)) next.add(`collapse:${step.id}`)
  } else {
    next.delete(`collapse:${step.id}`)
    next.add(step.id)
  }
  openDetailIds.value = next
}

function isStepDone(step: ProcessStep): boolean {
  if (step.type === 'thinking') return step.status === 'done'
  return step.tool.status === 'done'
}

function isStepRunning(step: ProcessStep): boolean {
  if (step.type === 'thinking') return step.status === 'running'
  return step.tool.status === 'running'
}

function stepTitle(step: ProcessStep): string {
  if (step.type === 'thinking') {
    return isStepRunning(step) ? '思考中' : '思考'
  }
  return step.tool.name || '工具'
}

function stepStatusText(step: ProcessStep): string {
  if (step.type === 'thinking') {
    return isStepRunning(step) ? '' : '已完成'
  }
  return step.tool.status === 'running' ? '执行中' : '完成'
}

function formatValue(value: unknown, empty: string): string {
  if (value === undefined || value === null) return empty
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed || empty
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function toolInput(tool: ToolStep): string {
  return formatValue(tool.arguments, '无参数')
}

function toolResult(tool: ToolStep): string {
  if (tool.result !== undefined) return formatValue(tool.result, '')
  if (tool.status === 'running') return '执行中…'
  return '无结果'
}

/** 步骤从 running → done 时清掉强制收起标记 */
watch(
  () =>
    steps.value
      .map((s) => `${s.id}:${isStepRunning(s) ? 'r' : 'd'}`)
      .join(','),
  () => {
    const next = new Set(openDetailIds.value)
    let changed = false
    for (const step of steps.value) {
      if (!isStepRunning(step) && next.has(`collapse:${step.id}`)) {
        next.delete(`collapse:${step.id}`)
        changed = true
      }
    }
    if (changed) openDetailIds.value = next
  },
)

/** 结束后整体收起时清空详情展开态，再次展开只看紧凑列表 */
watch(
  () => props.turn.processExpanded,
  (open) => {
    if (!open && !isInProgress.value) {
      openDetailIds.value = new Set()
    }
  },
)
</script>

<template>
  <div v-if="steps.length" class="af-process">
    <button
      type="button"
      class="af-process__header"
      :aria-expanded="turn.processExpanded"
      @click="emit('toggle')"
    >
      <span class="af-process__title">
        <el-icon class="af-process__header-icon"><Tools /></el-icon>
        <span class="af-process__label">{{ headerLabel }}</span>
        <el-icon
          class="af-process__expand"
          :class="{ 'af-process__expand--collapsed': !turn.processExpanded }"
        >
          <ArrowDown />
        </el-icon>
      </span>
    </button>

    <div
      class="af-process__body"
      :class="{ 'af-process__body--open': turn.processExpanded }"
    >
      <div class="af-process__body-inner">
        <div class="af-process__list">
          <div
            v-for="step in steps"
            :key="step.id"
            class="af-process__item"
            :class="{
              'af-process__item--running': isStepRunning(step),
              'af-process__item--open': isDetailOpen(step),
            }"
          >
            <button
              type="button"
              class="af-process__row"
              @click.stop="toggleDetail(step)"
            >
              <span
                class="af-process__dot"
                :class="{
                  'af-process__dot--done': isStepDone(step),
                  'af-process__dot--running': isStepRunning(step),
                }"
              />
              <span class="af-process__row-name">{{ stepTitle(step) }}</span>
              <span v-if="stepStatusText(step)" class="af-process__row-status">
                {{ stepStatusText(step) }}
              </span>
              <el-icon class="af-process__row-chevron">
                <ArrowDown v-if="isDetailOpen(step)" />
                <ArrowRight v-else />
              </el-icon>
            </button>

            <div v-if="isDetailOpen(step)" class="af-process__item-detail">
              <template v-if="step.type === 'thinking'">
                <div
                  class="af-process__thinking"
                  :class="{ 'af-process__thinking--live': isStepRunning(step) }"
                >
                  <MarkdownView :content="turn.reasoning" />
                </div>
              </template>
              <template v-else>
                <div class="af-process__section-title">参数</div>
                <pre class="af-process__section-text">{{ toolInput(step.tool) }}</pre>
                <div class="af-process__section-title">结果</div>
                <pre class="af-process__section-text">{{ toolResult(step.tool) }}</pre>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
