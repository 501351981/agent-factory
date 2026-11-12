<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

const props = withDefaults(
  defineProps<{
    modelValue: string
    rows?: number
    placeholder?: string
    /** Editor mode: JSON gets format button; markdown is plain text friendly. */
    mode?: 'json' | 'markdown'
    /** @deprecated use mode="json" */
    json?: boolean
  }>(),
  {
    rows: 14,
    placeholder: '',
    mode: undefined,
    json: undefined,
  },
)

const resolvedMode = computed(() => {
  if (props.mode) return props.mode
  if (props.json === false) return 'markdown'
  return 'json'
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const textareaRef = ref<HTMLTextAreaElement | null>(null)
const local = ref(props.modelValue)

watch(
  () => props.modelValue,
  (v) => {
    if (v !== local.value) local.value = v
  },
)

const lineCount = computed(() => Math.max(1, (local.value || '').split('\n').length))

function onInput(event: Event) {
  const value = (event.target as HTMLTextAreaElement).value
  local.value = value
  emit('update:modelValue', value)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Tab' || event.isComposing) return
  event.preventDefault()
  const el = event.target as HTMLTextAreaElement
  const start = el.selectionStart
  const end = el.selectionEnd
  const insert = '  '
  const next = local.value.slice(0, start) + insert + local.value.slice(end)
  local.value = next
  emit('update:modelValue', next)
  void nextTick(() => {
    el.selectionStart = el.selectionEnd = start + insert.length
  })
}

function formatJson() {
  try {
    const parsed = JSON.parse(local.value || '[]')
    const pretty = JSON.stringify(parsed, null, 2)
    local.value = pretty
    emit('update:modelValue', pretty)
    ElMessage.success('已格式化')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : 'JSON 无效')
  }
}
</script>

<template>
  <div class="code-editor" :class="`code-editor--${resolvedMode}`">
    <div class="code-editor__toolbar">
      <span class="code-editor__hint">
        {{ resolvedMode === 'json' ? 'JSON · Tab 缩进' : 'Markdown · Tab 缩进' }}
      </span>
      <button
        v-if="resolvedMode === 'json'"
        type="button"
        class="code-editor__fmt"
        @click="formatJson"
      >
        格式化
      </button>
    </div>
    <div class="code-editor__body">
      <div class="code-editor__gutter" aria-hidden="true">
        <span v-for="n in lineCount" :key="n">{{ n }}</span>
      </div>
      <textarea
        ref="textareaRef"
        class="code-editor__textarea"
        :value="local"
        :rows="rows"
        :placeholder="placeholder"
        spellcheck="false"
        @input="onInput"
        @keydown="onKeydown"
      />
    </div>
  </div>
</template>

<style scoped>
.code-editor {
  width: 100%;
  border: 1px solid var(--af-border, #e5e7eb);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
  color: #1f2937;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.02);
}

.code-editor__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  background: #f8fafc;
  border-bottom: 1px solid var(--af-border, #e5e7eb);
}

.code-editor__hint {
  font-size: 11px;
  color: #94a3b8;
}

.code-editor__fmt {
  border: 1px solid #e2e8f0;
  background: #fff;
  color: #475569;
  border-radius: 6px;
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
  cursor: pointer;
}

.code-editor__fmt:hover {
  background: #f1f5f9;
  color: #0f172a;
  border-color: #cbd5e1;
}

.code-editor__body {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  min-height: 0;
}

.code-editor__gutter {
  padding: 12px 0;
  background: #f8fafc;
  border-right: 1px solid #eef2f7;
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  color: #94a3b8;
  user-select: none;
  overflow: hidden;
}

.code-editor__gutter span {
  display: block;
  padding-right: 8px;
}

.code-editor__textarea {
  width: 100%;
  min-height: 180px;
  margin: 0;
  padding: 12px;
  border: 0;
  outline: none;
  resize: vertical;
  background: #fff;
  color: #1f2937;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  tab-size: 2;
  white-space: pre;
  overflow: auto;
}

.code-editor__textarea::placeholder {
  color: #94a3b8;
}

.code-editor__textarea:focus {
  background: #fff;
}
</style>
