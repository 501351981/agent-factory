/**
 * 管理台登录态：登录成功后把 JWT 存在 localStorage，
 * 之后所有请求带 `Authorization: Bearer <token>`。
 */

const TOKEN_KEY = 'af_admin_token'
const USER_KEY = 'af_admin_user'
const EXP_KEY = 'af_admin_exp'

export type AdminSession = {
  token: string
  username: string
  /** ISO 时间字符串 */
  expiresAt: string
}

export function getAdminToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function getAdminUsername(): string {
  try {
    return localStorage.getItem(USER_KEY) || ''
  } catch {
    return ''
  }
}

export function getAdminExpiresAt(): number {
  try {
    const raw = Number(localStorage.getItem(EXP_KEY) || 0)
    return Number.isFinite(raw) ? raw : 0
  } catch {
    return 0
  }
}

/** 本地判断是否已登录（过期时间到点即视为失效）。 */
export function isLoggedIn(): boolean {
  if (!getAdminToken()) return false
  const exp = getAdminExpiresAt()
  if (!exp) return true
  // 提前 30 秒判定过期，避免边界上打出 401。
  return exp * 1000 - 30_000 > Date.now()
}

export function saveAdminSession(session: AdminSession): void {
  const exp = Date.parse(session.expiresAt)
  try {
    localStorage.setItem(TOKEN_KEY, session.token)
    localStorage.setItem(USER_KEY, session.username)
    localStorage.setItem(EXP_KEY, String(Number.isFinite(exp) ? Math.floor(exp / 1000) : 0))
  } catch {
    /* localStorage 不可用时退化为仅本次会话 */
  }
}

export function clearAdminSession(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    localStorage.removeItem(EXP_KEY)
  } catch {
    /* ignore */
  }
}

export function redirectToLogin(): void {
  clearAdminSession()
  if (window.location.pathname !== '/login') {
    const redirect = encodeURIComponent(window.location.pathname + window.location.search)
    window.location.replace(`/login?redirect=${redirect}`)
  }
}
