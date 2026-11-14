<script setup lang="ts">
/**
 * Contenteditable composer（对齐 demo/ai-chat-input AiComposer）：
 * 文本与不可编辑 skill chip 同行混排；/ 唤起技能菜单。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Paperclip, Top, VideoPause } from '@element-plus/icons-vue'
import type { ModelProfileDto, SkillDto } from './api'
import { buildSkillPrefixedMessage } from './skillMessage'

type Attachment = {
  id: string
  name: string
  size: number
  kind: 'image' | 'file'
  previewUrl?: string
  textNote: string
}

type ChipMeta = {
  occurrenceId: number
  source: 'skills'
  ref: string
  label: string
}

/** chip 在投影中的占位定界符（不会出现在正常输入里）。 */
const CHIP_TOKEN = '\u2402'

const props = defineProps<{
  modelValue: string
  modelProfileId?: string
  models?: ModelProfileDto[]
  skills?: SkillDto[]
  disabled?: boolean
  loading?: boolean
  submitDisabled?: boolean
  placeholder?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'update:modelProfileId', value: string): void
  (e: 'submit', value: string): void
  (e: 'stop'): void
}>()

const enabledModels = computed(() => (props.models || []).filter((m) => m.enabled))
const skillCatalog = computed(() =>
  (props.skills || []).filter((s) => (s.name || '').trim()),
)
const selectedModelLabel = computed(() => {
  const hit = enabledModels.value.find((m) => m.id === props.modelProfileId)
  if (!hit) return '选择模型'
  return hit.name || '未命名模型'
})

const rootRef = ref<HTMLElement | null>(null)
const editorRef = ref<HTMLDivElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const focused = ref(false)
const composing = ref(false)
const draft = ref('')
const attachments = ref<Attachment[]>([])
let attachmentSeq = 0
let occurrenceSeq = 0
const chipMap = new Map<Element, ChipMeta>()

const slashOpen = ref(false)
const slashQuery = ref('')
const slashStart = ref(-1)
const slashEnd = ref(-1)
const slashIndex = ref(0)

const filteredSkills = computed(() => {
  const q = slashQuery.value.trim().toLowerCase()
  const list = skillCatalog.value
  if (!q) return list
  return list.filter((s) => {
    const name = s.name.toLowerCase()
    const desc = (s.description || '').toLowerCase()
    return name.includes(q) || desc.includes(q)
  })
})

const canSubmit = computed(
  () =>
    !props.disabled &&
    !props.submitDisabled &&
    (draft.value.replace(/\u00A0/g, ' ').trim().length > 0 ||
      attachments.value.length > 0 ||
      collectSkillRefs().length > 0),
)

const placeholderText = computed(
  () => props.placeholder || '输入消息，/ 选择技能，Enter 发送',
)

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function isChipEl(node: Node): node is HTMLElement {
  return node instanceof HTMLElement && node.dataset.composerChip !== undefined
}

function syncProjection(): void {
  const editor = editorRef.value
  if (!editor) return
  let text = ''
  const walk = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? ''
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const el = node as Element
    if (isChipEl(el)) {
      text += `${CHIP_TOKEN}${el.textContent ?? ''}${CHIP_TOKEN}`
      return
    }
    if (
      (el.tagName === 'DIV' || el.tagName === 'P' || el.tagName === 'BR') &&
      node !== editor &&
      text.length > 0 &&
      !text.endsWith('\n')
    ) {
      text += '\n'
    }
    el.childNodes.forEach(walk)
  }
  editor.childNodes.forEach(walk)
  draft.value = text
  emit('update:modelValue', text.replace(new RegExp(`${CHIP_TOKEN}[^${CHIP_TOKEN}]*${CHIP_TOKEN}`, 'g'), (m) => {
    const inner = m.slice(1, -1)
    return `/${inner.replace(/^\//, '')}`
  }).replace(/\u00A0/g, ' '))
}

function resolveDomPosition(
  editor: HTMLElement,
  target: number,
): { node: Node; offset: number } {
  let remaining = target
  const walk = (node: Node): { node: Node; offset: number } | null => {
    if (node.nodeType === Node.TEXT_NODE) {
      const len = node.textContent?.length ?? 0
      if (remaining <= len) return { node, offset: remaining }
      remaining -= len
      return null
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return null
    const el = node as Element
    if (isChipEl(el)) {
      const len = 1 + (el.textContent?.length ?? 0) + 1
      if (remaining < len) {
        return {
          node: el.parentNode!,
          offset: Array.from(el.parentNode!.childNodes).indexOf(el as ChildNode) + 1,
        }
      }
      remaining -= len
      return null
    }
    for (const child of Array.from(node.childNodes)) {
      const hit = walk(child)
      if (hit) return hit
    }
    return null
  }
  return (
    walk(editor) ?? {
      node: editor.childNodes[editor.childNodes.length - 1] ?? editor,
      offset: editor.childNodes.length,
    }
  )
}

function caretProjectionOffset(): number {
  const sel = window.getSelection()
  const editor = editorRef.value
  if (!sel || sel.rangeCount === 0 || !editor) return draft.value.length
  const range = sel.getRangeAt(0)
  if (!editor.contains(range.startContainer)) return draft.value.length

  let text = ''
  let found = false

  const appendNode = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? ''
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const el = node as Element
    if (isChipEl(el)) {
      text += `${CHIP_TOKEN}${el.textContent ?? ''}${CHIP_TOKEN}`
      return
    }
    if (
      (el.tagName === 'DIV' || el.tagName === 'P') &&
      node !== editor &&
      text.length > 0 &&
      !text.endsWith('\n')
    ) {
      text += '\n'
    }
    for (const child of Array.from(node.childNodes)) appendNode(child)
  }

  const visit = (node: Node): boolean => {
    if (found) return true
    if (node === range.startContainer) {
      if (node.nodeType === Node.TEXT_NODE) {
        text += (node.textContent ?? '').slice(0, range.startOffset)
      } else {
        const children = Array.from(node.childNodes)
        for (let i = 0; i < range.startOffset && i < children.length; i++) {
          appendNode(children[i]!)
        }
      }
      found = true
      return true
    }
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? ''
      return false
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return false
    const el = node as Element
    if (isChipEl(el)) {
      text += `${CHIP_TOKEN}${el.textContent ?? ''}${CHIP_TOKEN}`
      return false
    }
    if (
      (el.tagName === 'DIV' || el.tagName === 'P') &&
      node !== editor &&
      text.length > 0 &&
      !text.endsWith('\n')
    ) {
      text += '\n'
    }
    for (const child of Array.from(node.childNodes)) {
      if (visit(child)) return true
    }
    return false
  }

  for (const child of Array.from(editor.childNodes)) {
    if (visit(child)) break
  }
  return text.length
}

function buildChipFace(label: string): DocumentFragment {
  const frag = document.createDocumentFragment()
  const icon = document.createElement('span')
  icon.className = 'chip-icon'
  icon.textContent = '⚡'
  const labelEl = document.createElement('span')
  labelEl.className = 'chip-label'
  labelEl.textContent = label.startsWith('/') ? label : `/${label}`
  frag.append(icon, labelEl)
  return frag
}

function insertSkillChip(skill: SkillDto, span: { start: number; end: number }): void {
  const editor = editorRef.value
  if (!editor) return
  const startDom = resolveDomPosition(editor, span.start)
  const endDom = resolveDomPosition(editor, span.end)
  const range = document.createRange()
  range.setStart(startDom.node, startDom.offset)
  range.setEnd(endDom.node, endDom.offset)

  const el = document.createElement('span')
  el.dataset.composerChip = ''
  el.contentEditable = 'false'
  occurrenceSeq += 1
  chipMap.set(el, {
    occurrenceId: occurrenceSeq,
    source: 'skills',
    ref: skill.name,
    label: skill.name,
  })
  el.appendChild(buildChipFace(skill.name))
  range.deleteContents()
  range.insertNode(el)
  const space = document.createTextNode('\u00A0')
  el.after(space)
  const sel = window.getSelection()
  const after = document.createRange()
  after.setStart(space, 1)
  after.collapse(true)
  sel?.removeAllRanges()
  sel?.addRange(after)
  syncProjection()
}

function closeSlashMenu(): void {
  slashOpen.value = false
  slashQuery.value = ''
  slashStart.value = -1
  slashEnd.value = -1
}

function detectSlash(value: string, caret: number): { start: number; end: number; query: string } | null {
  if (!skillCatalog.value.length) return null
  const before = value.slice(0, caret)
  // 投影里 chip 占位不含 `/query` 触发；只匹配真实文本里的 /
  const match = before.match(/(^|[\s\n\u00A0])\/([a-zA-Z0-9_-]*)$/)
  if (!match) return null
  const query = match[2] || ''
  const start = before.length - query.length - 1
  return { start, end: caret, query }
}

function trackSlash(): void {
  syncProjection()
  const caret = caretProjectionOffset()
  const hit = detectSlash(draft.value, caret)
  if (!hit) {
    closeSlashMenu()
    return
  }
  slashOpen.value = true
  slashQuery.value = hit.query
  slashStart.value = hit.start
  slashEnd.value = hit.end
  slashIndex.value = 0
}

function pickSkill(skill: SkillDto): void {
  if (slashStart.value < 0) return
  insertSkillChip(skill, { start: slashStart.value, end: slashEnd.value })
  closeSlashMenu()
}

function collectOccurrences(): Array<ChipMeta & { offset: number; length: number }> {
  const editor = editorRef.value
  const out: Array<ChipMeta & { offset: number; length: number }> = []
  if (!editor) return out
  let text = ''
  const walk = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? ''
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const el = node as Element
    if (isChipEl(el)) {
      const meta = chipMap.get(el)
      if (meta) {
        out.push({
          ...meta,
          offset: text.length + 1,
          length: (el.textContent?.length ?? 0) + 2,
        })
      }
      text += `${CHIP_TOKEN}${el.textContent ?? ''}${CHIP_TOKEN}`
      return
    }
    el.childNodes.forEach(walk)
  }
  editor.childNodes.forEach(walk)
  return out
}

function collectSkillRefs(): string[] {
  const names: string[] = []
  for (const occ of collectOccurrences()) {
    if (occ.source === 'skills' && !names.includes(occ.ref)) names.push(occ.ref)
  }
  return names
}

function clearEditor(): void {
  const editor = editorRef.value
  if (editor) editor.innerHTML = ''
  chipMap.clear()
  draft.value = ''
  emit('update:modelValue', '')
  closeSlashMenu()
}

async function setText(text: string): Promise<void> {
  await nextTick()
  const value = String(text || '')
  const editor = editorRef.value
  chipMap.clear()
  closeSlashMenu()
  if (editor) {
    editor.textContent = value
    // Place caret at end.
    const selection = window.getSelection()
    const range = document.createRange()
    range.selectNodeContents(editor)
    range.collapse(false)
    selection?.removeAllRanges()
    selection?.addRange(range)
  }
  draft.value = value
  emit('update:modelValue', value)
  await focus()
}

function buildOutgoingText(): string {
  const occurrences = collectOccurrences()
  let serialized = draft.value
  for (const occ of [...occurrences].sort((a, b) => b.offset - a.offset)) {
    const start = occ.offset - 1
    const token = occ.source === 'skills' ? `/${occ.ref}` : `@${occ.ref}`
    serialized = serialized.slice(0, start) + token + serialized.slice(start + occ.length)
  }
  serialized = serialized.replace(/\u00A0/g, ' ').trim()

  serialized = buildSkillPrefixedMessage(serialized, collectSkillRefs())

  const notes = attachments.value.map((a) => a.textNote).filter(Boolean)
  if (!notes.length) return serialized
  if (!serialized) return notes.join('\n')
  return `${serialized}\n\n${notes.join('\n')}`
}

function submit(): void {
  if (!canSubmit.value) return
  closeSlashMenu()
  const text = buildOutgoingText()
  if (!text.trim()) return
  emit('submit', text)
  for (const a of attachments.value) {
    if (a.previewUrl) URL.revokeObjectURL(a.previewUrl)
  }
  attachments.value = []
  clearEditor()
  void nextTick(() => editorRef.value?.focus())
}

function onEditorInput(): void {
  if (props.disabled) return
  trackSlash()
}

function onKeyDown(e: KeyboardEvent): void {
  if (composing.value || props.disabled) return

  if (slashOpen.value && filteredSkills.value.length) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      slashIndex.value = (slashIndex.value + 1) % filteredSkills.value.length
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      slashIndex.value =
        (slashIndex.value - 1 + filteredSkills.value.length) % filteredSkills.value.length
      return
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      const skill = filteredSkills.value[slashIndex.value]
      if (skill) pickSkill(skill)
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      closeSlashMenu()
      return
    }
  }

  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

function onPaste(e: ClipboardEvent): void {
  const files = e.clipboardData?.files
  if (files && files.length > 0) {
    e.preventDefault()
    void Promise.all(Array.from(files).map((f) => addAttachment(f)))
    return
  }
  const text = e.clipboardData?.getData('text/plain')
  if (text) {
    e.preventDefault()
    document.execCommand('insertText', false, text)
    trackSlash()
  }
}

function keepFocus(e: MouseEvent): void {
  e.preventDefault()
}

async function fileToDataUrl(file: File): Promise<string> {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function addAttachment(file: File): Promise<void> {
  const id = `att-${++attachmentSeq}`
  const kind = file.type.startsWith('image/') ? 'image' : 'file'
  let textNote = `[附件: ${file.name}]`
  let previewUrl: string | undefined
  if (kind === 'image') {
    previewUrl = URL.createObjectURL(file)
    try {
      const dataUrl = await fileToDataUrl(file)
      if (dataUrl.length < 120_000) {
        textNote = `[图片附件: ${file.name}]\n${dataUrl}`
      } else {
        textNote = `[图片附件: ${file.name}（较大，已仅附文件名；后续可接多模态上传）]`
      }
    } catch {
      textNote = `[图片附件: ${file.name}]`
    }
  }
  attachments.value = [
    ...attachments.value,
    { id, name: file.name, size: file.size, kind, previewUrl, textNote },
  ]
}

function removeAttachment(id: string): void {
  const target = attachments.value.find((a) => a.id === id)
  if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
  attachments.value = attachments.value.filter((a) => a.id !== id)
}

function openFilePicker(): void {
  fileInputRef.value?.click()
}

function onFilePicked(e: Event): void {
  const input = e.target as HTMLInputElement
  if (input.files) {
    void Promise.all(Array.from(input.files).map((f) => addAttachment(f)))
  }
  input.value = ''
}

function onDocPointerDown(ev: PointerEvent): void {
  const root = rootRef.value
  if (!root) return
  if (root.contains(ev.target as Node)) return
  focused.value = false
  closeSlashMenu()
}

watch(filteredSkills, (list) => {
  if (slashIndex.value >= list.length) {
    slashIndex.value = Math.max(0, list.length - 1)
  }
})

watch(
  () => props.skills,
  () => closeSlashMenu(),
)

watch(
  () => props.modelValue,
  (v) => {
    // 父级清空（发送后）时同步清空编辑器
    if (!v && draft.value) {
      const hasChips = chipMap.size > 0
      const editorEmpty = !editorRef.value?.textContent?.trim()
      if (!hasChips && editorEmpty) {
        draft.value = ''
      } else if (!v.trim()) {
        clearEditor()
      }
    }
  },
)

watch(
  () => props.disabled,
  (disabled) => {
    if (editorRef.value) editorRef.value.contentEditable = disabled ? 'false' : 'true'
  },
)

onMounted(() => {
  document.addEventListener('pointerdown', onDocPointerDown)
  if (editorRef.value) {
    editorRef.value.contentEditable = props.disabled ? 'false' : 'true'
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointerDown)
})

async function focus(): Promise<void> {
  await nextTick()
  if (props.disabled) return
  const el = editorRef.value
  if (!el) return
  el.contentEditable = 'true'
  el.focus()
}

defineExpose({ focus, setText })
</script>

<template>
  <div ref="rootRef" class="af-composer">
    <div
      class="af-composer__card"
      data-composer-card
      :class="{ 'is-focused': focused, 'is-disabled': disabled }"
    >
      <div
        v-if="slashOpen"
        class="af-composer__slash"
        role="listbox"
        aria-label="技能列表"
        @mousedown.prevent
      >
        <div v-if="!filteredSkills.length" class="af-composer__slash-empty">无匹配技能</div>
        <button
          v-for="(skill, idx) in filteredSkills"
          :key="skill.name"
          type="button"
          class="af-composer__slash-item"
          :class="{ 'is-active': idx === slashIndex }"
          role="option"
          :aria-selected="idx === slashIndex"
          @mouseenter="slashIndex = idx"
          @click="pickSkill(skill)"
        >
          <span class="af-composer__slash-name">/{{ skill.name }}</span>
          <span class="af-composer__slash-desc">{{ skill.description || '无描述' }}</span>
        </button>
      </div>

      <div v-if="attachments.length" class="af-composer__attachments">
        <div v-for="att in attachments" :key="att.id" class="af-composer__att">
          <img v-if="att.previewUrl" :src="att.previewUrl" class="af-composer__thumb" alt="" />
          <span v-else class="af-composer__thumb af-composer__thumb--file">📄</span>
          <div class="af-composer__att-meta">
            <span class="af-composer__att-name">{{ att.name }}</span>
            <span class="af-composer__att-size">{{ formatSize(att.size) }}</span>
          </div>
          <button
            type="button"
            class="af-composer__att-remove"
            aria-label="移除附件"
            :disabled="disabled"
            @mousedown="keepFocus"
            @click="removeAttachment(att.id)"
          >
            ×
          </button>
        </div>
      </div>

      <div
        ref="editorRef"
        class="af-composer__editor"
        contenteditable="true"
        role="textbox"
        aria-multiline="true"
        :data-placeholder="placeholderText"
        :aria-label="placeholderText"
        @input="onEditorInput"
        @keydown="onKeyDown"
        @paste="onPaste"
        @focus="focused = true"
        @mouseup="trackSlash"
        @keyup="trackSlash"
        @compositionstart="composing = true"
        @compositionend="
          composing = false;
          trackSlash()
        "
      />

      <div class="af-composer__bar">
        <div class="af-composer__left">
          <label v-if="enabledModels.length" class="af-composer__model">
            <span class="af-composer__model-label">{{ selectedModelLabel }}</span>
            <select
              class="af-composer__model-select"
              :value="modelProfileId || ''"
              :disabled="disabled"
              :title="selectedModelLabel"
              @change="
                emit('update:modelProfileId', ($event.target as HTMLSelectElement).value)
              "
            >
              <option v-for="m in enabledModels" :key="m.id" :value="m.id">
                {{ m.name || '未命名模型' }}
              </option>
            </select>
          </label>
          <div class="af-composer__hints">
            <span v-if="skillCatalog.length" class="af-composer__hint"><kbd>/</kbd> 技能</span>
            <span class="af-composer__hint"><kbd>Enter</kbd> 发送</span>
            <span class="af-composer__hint"><kbd>⇧ Enter</kbd> 换行</span>
          </div>
        </div>
        <div class="af-composer__actions">
          <button
            type="button"
            class="af-composer__icon-btn"
            title="添加附件"
            :disabled="disabled"
            @mousedown.prevent
            @click="openFilePicker"
          >
            <el-icon><Paperclip /></el-icon>
          </button>
          <input
            ref="fileInputRef"
            type="file"
            multiple
            hidden
            accept="image/*,.txt,.md,.json,.csv,.log"
            @change="onFilePicked"
          />
          <button
            v-if="loading"
            type="button"
            class="af-composer__stop-btn"
            :title="submitDisabled ? '停止并取消当前等待' : '停止当前回复'"
            aria-label="停止"
            @mousedown.prevent
            @click="emit('stop')"
          >
            <el-icon><VideoPause /></el-icon>
          </button>
          <button
            v-else
            type="button"
            class="af-composer__send-btn"
            :disabled="!canSubmit"
            aria-label="发送"
            title="发送"
            @mousedown.prevent
            @click="submit"
          >
            <el-icon><Top /></el-icon>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style>
/* 命令式插入的 chip 在 contenteditable 内，不能 scoped */
.af-composer__editor [data-composer-chip] {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 220px;
  height: 22px;
  padding: 0 8px;
  margin: 0 1px;
  border-radius: 6px;
  background: #f4f4f5;
  border: 1px solid #e4e4e7;
  color: #27272a;
  font-size: 14px;
  font-weight: 600;
  vertical-align: baseline;
  white-space: nowrap;
  user-select: none;
  cursor: default;
}
.af-composer__editor [data-composer-chip] .chip-icon {
  font-size: 14px;
}
.af-composer__editor [data-composer-chip] .chip-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

<style scoped>
.af-composer {
  position: relative;
  padding: 0 16px 12px;
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--af-composer-bg, var(--af-bg)) 0%, transparent) 0%,
    var(--af-composer-bg, var(--af-bg)) 28%
  );
  border-top: none;
  max-width: none;
  width: 100%;
  margin: 0;
  box-sizing: border-box;
}

.af-composer__card {
  position: relative;
  background: var(--af-composer-card, #fff);
  border: 1px solid var(--af-composer-border, transparent);
  border-radius: var(--af-radius-lg, 26px);
  padding: 12px 14px 10px;
  box-shadow: var(--af-shadow-composer, 0 0 0 1px rgba(0, 0, 0, 0.06), 0 8px 24px rgba(0, 0, 0, 0.06));
  color: var(--af-composer-text, var(--af-text));
  transition: box-shadow 0.2s ease;
}

.af-composer__card.is-focused {
  box-shadow: var(
    --af-shadow-composer-focus,
    0 0 0 1px rgba(0, 0, 0, 0.1),
    0 12px 32px rgba(0, 0, 0, 0.1)
  );
}

.af-composer__card.is-disabled {
  opacity: 0.72;
}

.af-composer__slash {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: calc(100% + 8px);
  z-index: 20;
  max-height: 260px;
  overflow-y: auto;
  background: var(--af-composer-card, #fff);
  border: 1px solid var(--af-composer-border, #e5e5e5);
  border-radius: var(--af-radius-md, 16px);
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.12);
  padding: 6px;
  color: var(--af-composer-text, var(--af-text));
}

.af-composer__slash-empty {
  padding: 10px 12px;
  font-size: 14px;
  color: var(--af-composer-muted, #a3a3a3);
}

.af-composer__slash-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  border: none;
  background: transparent;
  border-radius: 10px;
  padding: 9px 12px;
  cursor: pointer;
  text-align: left;
}

.af-composer__slash-item:hover,
.af-composer__slash-item.is-active {
  background: var(--af-accent-soft, #f5f5f5);
}

.af-composer__slash-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--af-composer-text, var(--af-text));
}

.af-composer__slash-desc {
  font-size: 14px;
  color: var(--af-composer-muted, #737373);
  line-height: 1.4;
}

.af-composer__attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 2px 2px 10px;
}

.af-composer__att {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #fafafa;
  border: 1px solid #e5e5e5;
  border-radius: 12px;
  padding: 6px 8px;
  max-width: 240px;
}

.af-composer__thumb {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
}

.af-composer__thumb--file {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e5e5e5;
  font-size: 14px;
}

.af-composer__att-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.af-composer__att-name {
  font-size: 14px;
  color: #0d0d0d;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.af-composer__att-size {
  font-size: 14px;
  color: #a3a3a3;
}

.af-composer__att-remove {
  background: none;
  border: none;
  color: #a3a3a3;
  font-size: 15px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
}

.af-composer__att-remove:hover:not(:disabled) {
  color: #0d0d0d;
}

.af-composer__editor {
  min-height: 48px;
  max-height: 200px;
  overflow-y: auto;
  padding: 6px 6px 4px;
  font: inherit;
  font-size: 15px;
  line-height: 1.65;
  color: var(--af-composer-text, var(--af-text, #0d0d0d));
  outline: none;
  word-break: break-word;
  white-space: pre-wrap;
}

.af-composer__editor:empty::before {
  content: attr(data-placeholder);
  color: var(--af-composer-muted, #a3a3a3);
  pointer-events: none;
}

.af-composer__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 4px;
}

.af-composer__left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.af-composer__model {
  position: relative;
  display: inline-flex;
  align-items: center;
  max-width: min(220px, 46vw);
  height: 30px;
  padding: 0 12px;
  border: 1px solid #e5e5e5;
  border-radius: 999px;
  background: #fafafa;
  color: #404040;
  font-size: 14px;
  cursor: pointer;
  overflow: hidden;
  transition:
    background 0.15s ease,
    border-color 0.15s ease;
}

.af-composer__model:hover {
  border-color: #d4d4d4;
  background: #f5f5f5;
}

.af-composer__model-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  pointer-events: none;
  padding-right: 14px;
}

.af-composer__model-label::after {
  content: '▾';
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: #a3a3a3;
  font-size: 14px;
}

.af-composer__model-select {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
  width: 100%;
  height: 100%;
  border: none;
  background: transparent;
}

.af-composer__hints {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 0;
}

.af-composer__hint {
  font-size: 14px;
  color: #a3a3a3;
}

.af-composer__hint kbd {
  border: 1px solid #e5e5e5;
  border-radius: 4px;
  padding: 0 4px;
  font-size: 14px;
  margin-right: 2px;
  background: #fafafa;
  color: #737373;
}

.af-composer__actions {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-shrink: 0;
}

.af-composer__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: #737373;
  cursor: pointer;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}

.af-composer__icon-btn:hover:not(:disabled) {
  background: #f5f5f5;
  color: #0d0d0d;
}

.af-composer__icon-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.af-composer__send-btn,
.af-composer__stop-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  transition:
    opacity 0.15s ease,
    transform 0.12s ease,
    background 0.15s ease;
}

.af-composer__send-btn {
  background: var(--af-composer-send-bg, #0d0d0d);
  color: var(--af-composer-send-text, #fff);
}

.af-composer__send-btn:disabled {
  background: var(--af-accent-soft, #e5e5e5);
  color: var(--af-composer-muted, #a3a3a3);
  opacity: 1;
  cursor: default;
}

.af-composer__send-btn:not(:disabled):hover {
  transform: scale(1.05);
  filter: brightness(1.08);
}

.af-composer__stop-btn {
  background: var(--af-composer-send-bg, #0d0d0d);
  color: var(--af-composer-send-text, #fff);
}

.af-composer__stop-btn:hover {
  filter: brightness(1.08);
}

@media (max-width: 640px) {
  .af-composer {
    padding: 0 12px 10px;
  }

  .af-composer__card {
    border-radius: 22px;
    padding: 10px 12px 8px;
  }

  .af-composer__hints {
    display: none;
  }

  .af-composer__editor {
    font-size: 15px;
    min-height: 40px;
  }
}
</style>
