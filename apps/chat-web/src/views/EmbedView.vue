<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AgentChat } from '@agent-factory/chat-ui'
import { API_BASE, API_KEY } from '../env'

const route = useRoute()
const router = useRouter()

const agentId = computed(() => String(route.query.agentId || ''))
const sessionId = computed(() => {
  const value = route.query.sessionId
  return value ? String(value) : undefined
})
const userKey = computed(() => {
  const value = route.query.userKey
  return value ? String(value) : undefined
})
const locale = computed(() => {
  const value = route.query.locale || route.query.lang
  return value ? String(value) : 'zh'
})
/** Local Bridge device bound to this session (client tools / skills). */
const clientDeviceId = computed(() => {
  const value = route.query.deviceId || route.query.clientDeviceId
  return value ? String(value) : undefined
})
/** Short-lived embed JWT from business backend; falls back to platform key for local dev. */
const accessToken = computed(() => {
  const token = route.query.token || route.query.embedToken
  if (token) return String(token)
  return API_KEY || undefined
})
/**
 * Local open mode: empty FACTORY_API_KEY means server skips auth.
 * Only block when a token/key is expected but missing.
 */
const missingAuth = computed(() => {
  const hasQueryToken = !!(route.query.token || route.query.embedToken)
  if (hasQueryToken || API_KEY) return !accessToken.value
  return false
})
/** Demo / business: pass template vars like userId into session create. */
const promptVars = computed(() => {
  const userId = route.query.userId
  if (!userId) return undefined
  return { userId: String(userId) }
})

function onSession(nextSessionId: string) {
  if (route.query.sessionId === nextSessionId) return
  router.replace({
    path: '/embed',
    query: {
      ...route.query,
      sessionId: nextSessionId,
    },
  })
}
</script>

<template>
  <div class="embed-shell">
    <el-result
      v-if="!agentId"
      icon="warning"
      title="缺少 agentId"
      sub-title="请使用 /embed?agentId=...&userKey=...&deviceId=demo-mac 打开嵌入页"
    />
    <el-result
      v-else-if="missingAuth"
      icon="warning"
      title="缺少 embed token"
      sub-title="请由业务后端用 AK/SK 调用 /api/auth/embed-token 后传入 token"
    />
    <AgentChat
      v-else
      :base-url="API_BASE"
      :agent-id="agentId"
      :session-id="sessionId"
      :api-key="accessToken"
      :user-key="userKey"
      :locale="locale"
      :prompt-vars="promptVars"
      :client-device-id="clientDeviceId"
      :show-header="false"
      session-create-mode="host"
      @session="onSession"
    />
  </div>
</template>
