<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ChatDotRound,
  Coin,
  Collection,
  Connection,
  Cpu,
  Expand,
  Fold,
  Link,
  Monitor,
  Notebook,
  SwitchButton,
  Timer,
} from '@element-plus/icons-vue'
import { authApi } from './api/client'
import { clearAdminSession, getAdminToken, getAdminUsername, redirectToLogin } from './auth'

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)

const isLoginPage = computed(() => route.name === 'login')
const username = ref(getAdminUsername())

const menus = [
  { index: '/', title: '智能体管理', icon: Monitor },
  { index: '/teams', title: '多 Agent 团队', icon: Connection },
  { index: '/platform-skills', title: '平台技能', icon: Notebook },
  { index: '/models', title: '模型管理', icon: Cpu },
  { index: '/usage', title: '用量与花费', icon: Coin },
  { index: '/sessions', title: '会话记录', icon: ChatDotRound },
  { index: '/schedules', title: '定时任务', icon: Timer },
  { index: '/memories', title: '记忆', icon: Collection },
]

const activeMenu = computed(() => {
  if (route.path.startsWith('/sessions')) return '/sessions'
  if (route.path.startsWith('/schedules')) return '/schedules'
  if (route.path.startsWith('/memories')) return '/memories'
  if (route.path.startsWith('/models')) return '/models'
  if (route.path.startsWith('/usage')) return '/usage'
  if (route.path.startsWith('/platform-skills')) return '/platform-skills'
  if (route.path.startsWith('/teams')) return '/teams'
  if (route.path.startsWith('/agents')) return '/'
  return '/'
})

const pageTitle = computed(() => {
  if (route.name === 'agent-edit') return '编辑智能体'
  return menus.find((item) => item.index === activeMenu.value)?.title || '控制台'
})

function onSelect(index: string) {
  router.push(index)
}

onMounted(async () => {
  if (isLoginPage.value || !getAdminToken()) return
  try {
    const session = await authApi.session()
    username.value = session.username
  } catch {
    // token 过期 / 服务端密码已更换 / 服务重启：回登录页重新认证。
    redirectToLogin()
  }
})

function logout() {
  clearAdminSession()
  ElMessage.success('已退出登录')
  router.replace({ name: 'login' })
}
</script>

<template>
  <router-view v-if="isLoginPage" />

  <el-container v-else class="admin-layout">
    <el-aside :width="collapsed ? '72px' : '232px'" class="admin-aside">
      <div class="admin-brand">
        <div class="admin-brand__mark">
          <el-icon :size="18" color="#fff"><Cpu /></el-icon>
        </div>
        <div v-if="!collapsed" class="admin-brand__text">
          <span class="admin-brand__name">Agent 工厂</span>
          <span class="admin-brand__sub">管理控制台</span>
        </div>
      </div>

      <el-menu
        class="admin-menu"
        :collapse="collapsed"
        :collapse-transition="false"
        :default-active="activeMenu"
        @select="onSelect"
      >
        <el-menu-item v-for="item in menus" :key="item.index" :index="item.index">
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>{{ item.title }}</template>
        </el-menu-item>
      </el-menu>

      <div class="admin-aside__footer">
        <el-button
          text
          style="color: rgba(255, 255, 255, 0.7); width: 100%"
          @click="collapsed = !collapsed"
        >
          <el-icon :size="18">
            <Fold v-if="!collapsed" />
            <Expand v-else />
          </el-icon>
          <span v-if="!collapsed" style="margin-left: 8px">收起菜单</span>
        </el-button>
      </div>
    </el-aside>

    <el-container>
      <el-header class="admin-header">
        <div class="admin-header__left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>控制台</el-breadcrumb-item>
            <el-breadcrumb-item>{{ pageTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="admin-header__links">
          <el-tag effect="plain" type="info" size="small" round>本地开发</el-tag>
          <el-button tag="a" href="http://localhost:8801" target="_blank" :icon="Link" plain>
            聊天页
          </el-button>
          <el-button tag="a" href="http://localhost:8803" target="_blank" :icon="Monitor" plain>
            Demo
          </el-button>
          <el-divider direction="vertical" />
          <span v-if="username" class="admin-header__user">{{ username }}</span>
          <el-button :icon="SwitchButton" text @click="logout">退出登录</el-button>
        </div>
      </el-header>

      <el-main class="admin-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>
