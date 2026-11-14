<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ChatDotRound,
  Coin,
  Collection,
  Cpu,
  Expand,
  Fold,
  Link,
  Monitor,
  Notebook,
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)

const menus = [
  { index: '/', title: '智能体管理', icon: Monitor },
  { index: '/platform-skills', title: '平台技能', icon: Notebook },
  { index: '/models', title: '模型管理', icon: Cpu },
  { index: '/usage', title: '用量与花费', icon: Coin },
  { index: '/sessions', title: '会话记录', icon: ChatDotRound },
  { index: '/memories', title: '记忆', icon: Collection },
]

const activeMenu = computed(() => {
  if (route.path.startsWith('/sessions')) return '/sessions'
  if (route.path.startsWith('/memories')) return '/memories'
  if (route.path.startsWith('/models')) return '/models'
  if (route.path.startsWith('/usage')) return '/usage'
  if (route.path.startsWith('/platform-skills')) return '/platform-skills'
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
</script>

<template>
  <el-container class="admin-layout">
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
        </div>
      </el-header>

      <el-main class="admin-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>
