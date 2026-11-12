<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown } from './markdown'

const props = defineProps<{
  content: string
  inline?: boolean
}>()

const html = computed(() => renderMarkdown(props.content || ''))

function copyViaTextarea(text: string): boolean {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '0'
  ta.style.width = '1px'
  ta.style.height = '1px'
  ta.style.padding = '0'
  ta.style.border = 'none'
  ta.style.outline = 'none'
  ta.style.boxShadow = 'none'
  ta.style.background = 'transparent'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  ta.setSelectionRange(0, ta.value.length)
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(ta)
  return ok
}

async function copyText(text: string): Promise<boolean> {
  if (!text) return false
  // clipboard API 在 iframe / 非安全上下文 / 权限策略下常失败，失败后降级
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // fall through
    }
  }
  return copyViaTextarea(text)
}

async function onClick(event: MouseEvent): Promise<void> {
  const target = event.target
  if (!(target instanceof Element)) return
  const btn = target.closest('[data-md-code-copy]')
  if (!(btn instanceof HTMLElement)) return
  event.preventDefault()
  event.stopPropagation()
  const block = btn.closest('.af-md-code-block')
  const code = block?.querySelector('code')?.textContent ?? ''
  if (!code) {
    ElMessage.warning('没有可复制的内容')
    return
  }
  const ok = await copyText(code)
  if (ok) {
    const prev = btn.textContent
    btn.textContent = '已复制'
    ElMessage.success('已复制')
    window.setTimeout(() => {
      if (btn.isConnected) btn.textContent = prev || '复制'
    }, 1500)
  } else {
    ElMessage.error('复制失败，请手动选择复制')
  }
}
</script>

<template>
  <div
    class="af-md"
    :class="{ 'af-md--inline': inline }"
    v-html="html"
    @click="onClick"
  />
</template>
