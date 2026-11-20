<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Cpu, Lock, User } from '@element-plus/icons-vue'
import { authApi } from '../api/client'
import { isLoggedIn, saveAdminSession } from '../auth'

const router = useRouter()
const route = useRoute()
const loading = ref(false)
const form = reactive({ username: 'admin', password: '' })

onMounted(() => {
  // 已登录则直接回到目标页，避免重复输入。
  if (isLoggedIn()) {
    router.replace(targetPath())
  }
})

function targetPath(): string {
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')) {
    return redirect
  }
  return '/'
}

async function submit() {
  if (!form.username.trim()) {
    ElMessage.warning('请输入用户名')
    return
  }
  if (!form.password) {
    ElMessage.warning('请输入密码')
    return
  }
  loading.value = true
  try {
    const session = await authApi.login(form.username.trim(), form.password)
    saveAdminSession({
      token: session.token,
      username: session.username,
      expiresAt: session.expires_at,
    })
    ElMessage.success('登录成功')
    router.replace(targetPath())
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <div class="login-brand__mark">
          <el-icon :size="20" color="#fff"><Cpu /></el-icon>
        </div>
        <div>
          <div class="login-brand__name">Agent 工厂</div>
          <div class="login-brand__sub">管理控制台</div>
        </div>
      </div>

      <el-form class="login-form" label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="form.username" size="large" :prefix-icon="User" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            size="large"
            show-password
            :prefix-icon="Lock"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button
          class="login-submit"
          type="primary"
          size="large"
          :loading="loading"
          native-type="submit"
        >
          登录
        </el-button>
      </el-form>

      <p class="login-hint">
        账号密码由服务端 <code>ADMIN_USERNAME</code> / <code>ADMIN_PASSWORD</code> 配置。
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(900px 500px at 15% 10%, rgba(64, 158, 255, 0.18), transparent 60%),
    radial-gradient(700px 400px at 85% 90%, rgba(37, 99, 235, 0.16), transparent 60%),
    var(--af-bg, #f0f3f8);
}

.login-card {
  width: 100%;
  max-width: 380px;
  padding: 32px 28px 24px;
  border-radius: 18px;
  background: #fff;
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.06),
    0 24px 60px rgba(15, 23, 42, 0.12);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}

.login-brand__mark {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #3b82f6, #2563eb);
  box-shadow: 0 8px 18px rgba(37, 99, 235, 0.35);
}

.login-brand__name {
  font-size: 17px;
  font-weight: 650;
  letter-spacing: 0.2px;
}

.login-brand__sub {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 2px;
}

.login-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.login-submit {
  width: 100%;
  margin-top: 4px;
}

.login-hint {
  margin: 20px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: #94a3b8;
}

.login-hint code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: #f1f5f9;
  border-radius: 4px;
  padding: 1px 4px;
}
</style>
