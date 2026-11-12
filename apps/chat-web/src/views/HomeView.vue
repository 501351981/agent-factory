<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { API_BASE, API_KEY } from '../env'

type Agent = { id: string; name: string }

const router = useRouter()
const agents = ref<Agent[]>([])
const loading = ref(false)

const form = reactive({
  agentId: '',
  sessionId: '',
  userKey: 'demo-user',
  deviceId: 'demo-mac',
})

async function loadAgents() {
  loading.value = true
  try {
    const headers: Record<string, string> = {}
    if (API_KEY) headers['X-API-Key'] = API_KEY
    const resp = await fetch(`${API_BASE}/api/agents`, { headers })
    if (!resp.ok) throw new Error(await resp.text())
    agents.value = await resp.json()
    if (!form.agentId && agents.value[0]) {
      form.agentId = agents.value[0].id
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function openChat() {
  if (!form.agentId) {
    ElMessage.warning('请先选择智能体')
    return
  }
  if (!form.userKey.trim()) {
    ElMessage.warning('请填写 userKey（业务用户标识）')
    return
  }
  const query: Record<string, string> = {
    agentId: form.agentId,
    userKey: form.userKey.trim(),
  }
  if (form.sessionId.trim()) query.sessionId = form.sessionId.trim()
  if (form.deviceId.trim()) query.deviceId = form.deviceId.trim()
  router.push({ path: '/embed', query })
}

onMounted(loadAgents)
</script>

<template>
  <div class="home">
    <header class="home__nav">
      <div class="home__brand">
        <span class="home__mark" aria-hidden="true">
          <el-icon :size="16"><ChatDotRound /></el-icon>
        </span>
        <span class="home__brand-text">Agent 工厂</span>
      </div>
      <a class="home__admin" href="http://localhost:8802" target="_blank" rel="noopener">
        打开管理台
      </a>
    </header>

    <main class="home__main">
      <section class="home__hero">
        <p class="home__eyebrow">对话入口</p>
        <h1 class="home__title">开始一段对话</h1>
        <p class="home__subtitle">选择智能体，配置会话参数后进入聊天界面</p>
      </section>

      <div class="home__card" v-loading="loading">
        <el-form label-position="top" class="home__form" @submit.prevent="openChat">
          <el-form-item label="智能体">
            <el-select v-model="form.agentId" placeholder="请选择智能体" filterable style="width: 100%">
              <el-option
                v-for="agent in agents"
                :key="agent.id"
                :label="`${agent.name}`"
                :value="agent.id"
              >
                <div class="home__agent-opt">
                  <span>{{ agent.name }}</span>
                  <span class="home__agent-id">{{ agent.id.slice(0, 8) }}...</span>
                </div>
              </el-option>
            </el-select>
          </el-form-item>

          <el-form-item label="会话 ID（可选）">
            <el-input v-model="form.sessionId" placeholder="留空则自动创建新会话" clearable />
          </el-form-item>

          <el-form-item label="用户 Key">
            <el-input v-model="form.userKey" placeholder="业务用户稳定 ID，用于长期记忆" clearable />
          </el-form-item>

          <el-form-item label="本机 Bridge deviceId">
            <el-input
              v-model="form.deviceId"
              placeholder="与 demo-bridge --device-id 一致；多 Bridge 时必填"
              clearable
            />
          </el-form-item>

          <button type="submit" class="home__cta" @click="openChat">进入对话</button>
        </el-form>
      </div>
    </main>
  </div>
</template>

<style scoped>
.home {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(ellipse 80% 50% at 50% -10%, rgba(0, 0, 0, 0.04), transparent 55%),
    #f7f7f8;
  color: #0d0d0d;
}

.home__nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 24px;
  background: rgba(247, 247, 248, 0.85);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.home__brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.home__mark {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: linear-gradient(145deg, #1a1a1a 0%, #3f3f46 100%);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.home__brand-text {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.02em;
}

.home__admin {
  font-size: 14px;
  color: #525252;
  text-decoration: none;
  padding: 6px 12px;
  border-radius: 8px;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}

.home__admin:hover {
  background: rgba(0, 0, 0, 0.05);
  color: #0d0d0d;
}

.home__main {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px 64px;
  gap: 28px;
}

.home__hero {
  text-align: center;
  max-width: 420px;
  animation: home-fade-up 0.45s ease both;
}

.home__eyebrow {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #8e8e8e;
}

.home__title {
  margin: 0;
  font-size: 32px;
  font-weight: 650;
  letter-spacing: -0.04em;
  line-height: 1.2;
}

.home__subtitle {
  margin: 10px 0 0;
  font-size: 14px;
  color: #737373;
  line-height: 1.6;
}

.home__card {
  width: min(100%, 440px);
  background: #fff;
  border-radius: 20px;
  padding: 28px 28px 24px;
  box-shadow:
    0 0 0 1px rgba(0, 0, 0, 0.05),
    0 12px 40px rgba(0, 0, 0, 0.06);
  animation: home-fade-up 0.5s ease 0.05s both;
}

.home__form :deep(.el-form-item__label) {
  color: #404040;
  font-weight: 500;
  font-size: 14px;
}

.home__form :deep(.el-input__wrapper),
.home__form :deep(.el-select__wrapper) {
  border-radius: 10px;
  box-shadow: 0 0 0 1px #e5e5e5 inset;
}

.home__form :deep(.el-input__wrapper:hover),
.home__form :deep(.el-select__wrapper:hover) {
  box-shadow: 0 0 0 1px #d4d4d4 inset;
}

.home__form :deep(.el-input__wrapper.is-focus),
.home__form :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px #0d0d0d inset !important;
}

.home__agent-opt {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.home__agent-id {
  color: #a3a3a3;
  font-size: 14px;
}

.home__cta {
  width: 100%;
  height: 44px;
  margin-top: 8px;
  border: none;
  border-radius: 12px;
  background: #0d0d0d;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background 0.15s ease,
    transform 0.12s ease;
}

.home__cta:hover {
  background: #262626;
}

.home__cta:active {
  transform: scale(0.98);
}

@keyframes home-fade-up {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 640px) {
  .home__title {
    font-size: 26px;
  }

  .home__card {
    padding: 22px 18px 18px;
    border-radius: 16px;
  }

  .home__nav {
    padding: 0 14px;
  }
}
</style>
