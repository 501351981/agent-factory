import { marked, Renderer } from 'marked'
import DOMPurify from 'dompurify'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { highlightCode } from './highlight'

marked.setOptions({
  gfm: true,
  breaks: true,
})

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** ChatGPT-style fenced code: header (lang + copy) + syntax-highlighted pane */
function renderCodeBlock(text: string, lang?: string, escaped?: boolean): string {
  // marked may pass already-escaped text; highlight needs raw source
  const raw = (escaped
    ? text
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, '&')
    : text
  ).replace(/\n$/, '')
  const langName = (lang || '').trim().split(/\s+/)[0] || ''
  const { html: codeHtml, language: langLabel } = highlightCode(raw, langName)
  return (
    `<div class="af-md-code-block">` +
    `<div class="af-md-code-block__header">` +
    `<span class="af-md-code-block__lang">${escapeHtml(langLabel)}</span>` +
    `<button type="button" class="af-md-code-block__copy" data-md-code-copy="1">复制</button>` +
    `</div>` +
    `<pre class="af-md-code-block__pre"><code class="af-md-code-block__code hljs language-${escapeHtml(langLabel)}">${codeHtml}</code></pre>` +
    `</div>\n`
  )
}

function wrapTables(html: string): string {
  if (!html.includes('<table') || html.includes('af-md-table-wrap')) {
    return html
  }
  return html
    .replace(/<table(\s[^>]*)?>/gi, '<div class="af-md-table-wrap">\n<table$1>')
    .replace(/<\/table>/gi, '</table>\n</div>')
}

function normalizeMathSource(content: string): string {
  let result = content
  // $$ ... $$ with surrounding spaces/newlines
  result = result.replace(/\$\$\s+([\s\S]+?)\s+\$\$/g, (_m, formula: string) => `$$${formula}$$`)
  // $ ... $ with spaces (inline, no newline)
  result = result.replace(
    /([^$]|^)\$\s+([^$\n]+?)\s+\$([^$]|$)/g,
    (_m, before: string, formula: string, after: string) => `${before}$${formula}$${after}`,
  )
  return result
}

function processMathContent(tex: string): string {
  let result = tex.replace(/\\dots\b/g, '\\ldots')
  // \tag{2} / \tag 2 → visual approx
  result = result.replace(/\\tag\*?\s*(?:\{([^{}]+)\}|(\d+))/g, '\\qquad ($1$2)')
  return result
}

function renderKatex(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(processMathContent(tex.trim()), {
      throwOnError: false,
      displayMode,
      output: 'html',
      errorColor: '#cc0000',
      strict: 'ignore',
      trust: false,
    })
  } catch {
    return `<code class="af-md-math-error">${escapeHtml(tex)}</code>`
  }
}

type MathSlot = { tex: string; display: boolean }

/**
 * Extract math before markdown so `$` inside code is preserved,
 * and marked does not mangle TeX.
 */
function extractMath(source: string): { text: string; slots: MathSlot[] } {
  const slots: MathSlot[] = []
  const push = (tex: string, display: boolean) => {
    const idx = slots.length
    slots.push({ tex, display })
    // Private-use placeholders survive marked as plain text
    return `\uE000MATH${idx}\uE001`
  }

  // Protect fenced code + inline code first
  const protectedParts: string[] = []
  let text = source.replace(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`)/g, (block) => {
    const i = protectedParts.length
    protectedParts.push(block)
    return `\uE000CODE${i}\uE001`
  })

  // Block: $$...$$ or \[...\]
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_m, tex: string) => push(tex, true))
  text = text.replace(/\\\[([\s\S]+?)\\\]/g, (_m, tex: string) => push(tex, true))

  // Inline: $...$ or \(...\)
  // Avoid matching lone $ or empty; no newlines inside $...$
  text = text.replace(/(^|[^\\$])\$([^\s$][^$\n]*?[^\s$]|[^\s$])\$(?!\$)/g, (_m, before: string, tex: string) => {
    return `${before}${push(tex, false)}`
  })
  text = text.replace(/\\\(([\s\S]+?)\\\)/g, (_m, tex: string) => push(tex, false))

  // Restore code
  text = text.replace(/\uE000CODE(\d+)\uE001/g, (_m, i: string) => protectedParts[Number(i)] || '')

  return { text, slots }
}

function reinjectMath(html: string, slots: MathSlot[]): string {
  if (!slots.length) return html
  let out = html.replace(/\uE000MATH(\d+)\uE001/g, (_m, i: string) => {
    const slot = slots[Number(i)]
    if (!slot) return ''
    const rendered = renderKatex(slot.tex, slot.display)
    return slot.display ? `<div class="af-md-math af-md-math--block">${rendered}</div>` : rendered
  })
  // marked 常把块级占位符包进 <p>，解开非法嵌套
  out = out.replace(
    /<p>\s*(<div class="af-md-math af-md-math--block">[\s\S]*?<\/div>)\s*<\/p>/g,
    '$1',
  )
  return out
}

const renderer = new Renderer()

renderer.code = ({ text, lang, escaped }) => renderCodeBlock(text, lang, escaped)

renderer.link = ({ href, title, text }) => {
  const url = String(href || '').trim()
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
  const external = /^https?:\/\//i.test(url)
  const icon = external ? '<span class="af-md-link__icon" aria-hidden="true">↗</span>' : ''
  return (
    `<a class="af-md-link" href="${escapeHtml(url)}"${titleAttr} target="_blank" rel="noopener noreferrer">` +
    `<span class="af-md-link__text">${text}</span>${icon}</a>`
  )
}

export function renderMarkdown(source: string): string {
  if (!source) return ''
  const normalized = normalizeMathSource(source)
  const { text, slots } = extractMath(normalized)
  const raw = marked.parse(text, { renderer, async: false }) as string
  const withMath = reinjectMath(raw, slots)
  const withTables = wrapTables(withMath)
  return DOMPurify.sanitize(withTables, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ['target', 'rel', 'data-md-code-copy', 'aria-hidden', 'style'],
    ADD_TAGS: ['button'],
  })
}
