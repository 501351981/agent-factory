<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ArrowDown,
  Close,
  Document,
  FullScreen,
  Menu,
  RefreshRight,
} from '@element-plus/icons-vue'
import type { BoardArtifactDto } from './api'
import { buildArtifactTree, shortPathName } from './boardTree'
import BoardTreeNodes from './BoardTreeNodes.vue'
import MarkdownView from './MarkdownView.vue'

const props = defineProps<{
  artifacts: BoardArtifactDto[]
  loading?: boolean
  sessionBound?: boolean
  fullscreen?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'refresh'): void
  (e: 'toggle-fullscreen'): void
}>()

/** tree = 文件树概览；file = 单文件内容（WorkBuddy 式） */
const viewMode = ref<'tree' | 'file'>('tree')
const selectedId = ref<string | null>(null)
const switcherOpen = ref(false)
const expandedDirs = ref<Set<string>>(new Set())

const sorted = computed(() =>
  [...(props.artifacts || [])].sort((a, b) => a.path.localeCompare(b.path)),
)

const tree = computed(() => buildArtifactTree(sorted.value))

const selected = computed(() => {
  if (!selectedId.value) return null
  return sorted.value.find((a) => a.id === selectedId.value) || null
})

watch(
  () => props.artifacts,
  (list) => {
    if (!list?.length) {
      selectedId.value = null
      viewMode.value = 'tree'
      switcherOpen.value = false
      return
    }
    if (selectedId.value && !list.some((a) => a.id === selectedId.value)) {
      selectedId.value = null
      viewMode.value = 'tree'
    }
    const next = new Set(expandedDirs.value)
    for (const node of buildArtifactTree(list)) {
      if (node.kind === 'folder') next.add(node.id)
    }
    expandedDirs.value = next
  },
  { immediate: true },
)

function toggleDir(id: string) {
  const next = new Set(expandedDirs.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedDirs.value = next
}

function openFile(id: string) {
  selectedId.value = id
  viewMode.value = 'file'
  switcherOpen.value = false
}

function showTree() {
  viewMode.value = 'tree'
  switcherOpen.value = false
}

function fileLabel(art: BoardArtifactDto) {
  return art.title?.trim() || shortPathName(art.path)
}
</script>

<template>
  <div class="af-board" v-loading="loading">
    <template v-if="viewMode === 'tree'">
      <div class="af-board__header">
        <div class="af-board__title">
          <el-icon :size="16"><Document /></el-icon>
          <span>会话产物</span>
          <span v-if="sorted.length" class="af-board__count">{{ sorted.length }}</span>
        </div>
        <div class="af-board__actions">
          <button
            type="button"
            class="af-chat__icon-btn"
            title="刷新"
            aria-label="刷新产物"
            @click="emit('refresh')"
          >
            <el-icon :size="16"><RefreshRight /></el-icon>
          </button>
          <button
            type="button"
            class="af-chat__icon-btn"
            :class="{ 'af-chat__icon-btn--active': fullscreen }"
            :title="fullscreen ? '退出全屏' : '全屏查看产物'"
            :aria-label="fullscreen ? '退出全屏' : '全屏查看产物'"
            @click="emit('toggle-fullscreen')"
          >
            <el-icon :size="16"><FullScreen /></el-icon>
          </button>
          <button
            type="button"
            class="af-chat__icon-btn"
            title="关闭"
            aria-label="关闭产物面板"
            @click="emit('close')"
          >
            <el-icon :size="16"><Close /></el-icon>
          </button>
        </div>
      </div>

      <div v-if="!sessionBound" class="af-board__empty">
        发送消息创建会话后，团队协作产物会出现在这里。
      </div>
      <div v-else-if="!loading && !sorted.length" class="af-board__empty">
        暂无产物。子 Agent 通过 <code>board_write</code> 写入后会显示在此。
      </div>
      <div v-else class="af-board__tree" role="tree">
        <div class="af-board__tree-summary">文件树 · {{ sorted.length }} 个产物</div>
        <BoardTreeNodes
          :nodes="tree"
          :expanded="expandedDirs"
          :selected-id="selectedId"
          @toggle-dir="toggleDir"
          @open-file="openFile"
        />
      </div>
    </template>

    <template v-else-if="selected">
      <div class="af-board__header af-board__header--file">
        <div class="af-board__file-nav">
          <button
            type="button"
            class="af-chat__icon-btn"
            title="概览"
            aria-label="返回文件树"
            @click="showTree"
          >
            <el-icon :size="16"><Menu /></el-icon>
          </button>
          <div class="af-board__switcher">
            <button
              type="button"
              class="af-board__switcher-btn"
              :aria-expanded="switcherOpen"
              @click="switcherOpen = !switcherOpen"
            >
              <el-icon :size="14"><Document /></el-icon>
              <span class="af-board__switcher-name">{{ fileLabel(selected) }}</span>
              <el-icon :size="12" class="af-board__switcher-caret"><ArrowDown /></el-icon>
            </button>
            <div v-if="switcherOpen" class="af-board__switcher-menu" role="menu">
              <div class="af-board__switcher-head">产物</div>
              <div class="af-board__switcher-summary">文件树 · {{ sorted.length }}</div>
              <div class="af-board__switcher-tree">
                <BoardTreeNodes
                  :nodes="tree"
                  :expanded="expandedDirs"
                  :selected-id="selectedId"
                  compact
                  @toggle-dir="toggleDir"
                  @open-file="openFile"
                />
              </div>
            </div>
          </div>
        </div>
        <div class="af-board__actions">
          <button
            type="button"
            class="af-chat__icon-btn"
            title="刷新"
            aria-label="刷新产物"
            @click="emit('refresh')"
          >
            <el-icon :size="16"><RefreshRight /></el-icon>
          </button>
          <button
            type="button"
            class="af-chat__icon-btn"
            :class="{ 'af-chat__icon-btn--active': fullscreen }"
            :title="fullscreen ? '退出全屏' : '全屏查看产物'"
            :aria-label="fullscreen ? '退出全屏' : '全屏查看产物'"
            @click="emit('toggle-fullscreen')"
          >
            <el-icon :size="16"><FullScreen /></el-icon>
          </button>
          <button
            type="button"
            class="af-chat__icon-btn"
            title="关闭"
            aria-label="关闭产物面板"
            @click="emit('close')"
          >
            <el-icon :size="16"><Close /></el-icon>
          </button>
        </div>
      </div>

      <div class="af-board__preview">
        <div class="af-board__preview-head">
          <div class="af-board__preview-meta">
            {{ selected.path }} · v{{ selected.version }}
          </div>
        </div>
        <div class="af-board__preview-body">
          <MarkdownView :content="selected.content || '（空）'" />
        </div>
      </div>
      <div
        v-if="switcherOpen"
        class="af-board__switcher-backdrop"
        @click="switcherOpen = false"
      />
    </template>
  </div>
</template>
