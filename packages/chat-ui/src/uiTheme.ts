/** Agent ``ui_theme`` tokens → CSS variables for ``.af-chat``. */

export const THEME_CSS_VARS: Record<string, string> = {
  bg: '--af-bg',
  surface: '--af-surface',
  border: '--af-border',
  borderSoft: '--af-border-soft',
  text: '--af-text',
  muted: '--af-muted',
  accent: '--af-accent',
  accentSoft: '--af-accent-soft',
  userBubble: '--af-user-bubble',
  userBubbleText: '--af-user-bubble-text',
  userBubbleBorder: '--af-user-bubble-border',
  assistantBubble: '--af-assistant-bubble',
  assistantBubbleText: '--af-assistant-bubble-text',
  assistantBubbleBorder: '--af-assistant-bubble-border',
  composerBg: '--af-composer-bg',
  composerCard: '--af-composer-card',
  composerBorder: '--af-composer-border',
  composerText: '--af-composer-text',
  composerMuted: '--af-composer-muted',
  composerSendBg: '--af-composer-send-bg',
  composerSendText: '--af-composer-send-text',
  think: '--af-think',
  thinkSoft: '--af-think-soft',
  tool: '--af-tool',
  toolSoft: '--af-tool-soft',
  ok: '--af-ok',
  warn: '--af-warn',
  danger: '--af-danger',
  radiusLg: '--af-radius-lg',
  radiusMd: '--af-radius-md',
  radiusBubble: '--af-radius-bubble',
}

const PRESET_DARK: Record<string, string> = {
  preset: 'dark',
  bg: '#0f1115',
  surface: '#1a1d24',
  border: '#2a2f3a',
  borderSoft: 'rgba(255, 255, 255, 0.08)',
  text: '#f3f4f6',
  muted: '#9ca3af',
  accent: '#e5e7eb',
  accentSoft: '#252a33',
  userBubble: '#2563eb',
  userBubbleText: '#ffffff',
  userBubbleBorder: 'transparent',
  assistantBubble: '#1a1d24',
  assistantBubbleText: '#f3f4f6',
  assistantBubbleBorder: '#2a2f3a',
  composerBg: '#0f1115',
  composerCard: '#1a1d24',
  composerBorder: '#2a2f3a',
  composerText: '#f3f4f6',
  composerMuted: '#9ca3af',
  composerSendBg: '#3b82f6',
  composerSendText: '#ffffff',
  think: '#818cf8',
  thinkSoft: '#1e1b4b',
  tool: '#2dd4bf',
  toolSoft: '#134e4a',
  ok: '#4ade80',
  warn: '#fbbf24',
  danger: '#f87171',
}

const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/
const FUNC = /^(?:rgba?|hsla?)\(\s*[\d.%\s,/\-+]+\s*\)$/i
const LENGTH = /^\d+(?:\.\d+)?(?:px|rem|%)$/
const KEYWORD = /^(?:transparent|currentColor)$/i

function safeValue(raw: unknown): string | null {
  const text = String(raw ?? '').trim()
  if (!text || text.length > 64) return null
  if (/[;{}<>]|url\(|expression|@/i.test(text)) return null
  if (HEX.test(text) || FUNC.test(text) || LENGTH.test(text) || KEYWORD.test(text)) return text
  return null
}

/** Normalize server theme; expand ``preset: dark``. */
export function normalizeUiTheme(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== 'object') return {}
  const input = raw as Record<string, unknown>
  const out: Record<string, string> = {}
  const preset = String(input.preset || '')
    .trim()
    .toLowerCase()
  if (preset === 'dark') Object.assign(out, PRESET_DARK)
  else if (preset === 'light') out.preset = 'light'

  for (const key of Object.keys(THEME_CSS_VARS)) {
    if (!(key in input)) continue
    const safe = safeValue(input[key])
    if (safe) out[key] = safe
  }
  return out
}

/** Inline style object for Vue ``:style``. */
export function themeToCssVars(theme: Record<string, string> | null | undefined): Record<string, string> {
  const normalized = normalizeUiTheme(theme || {})
  const vars: Record<string, string> = {}
  for (const [key, cssName] of Object.entries(THEME_CSS_VARS)) {
    const val = normalized[key]
    if (val) vars[cssName] = val
  }
  return vars
}
