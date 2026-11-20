<script setup lang="ts">
import { Document, Folder, FolderOpened } from '@element-plus/icons-vue'
import type { BoardTreeNode } from './boardTree'

defineProps<{
  nodes: BoardTreeNode[]
  expanded: Set<string>
  selectedId?: string | null
  depth?: number
  compact?: boolean
}>()

defineEmits<{
  (e: 'toggle-dir', id: string): void
  (e: 'open-file', id: string): void
}>()
</script>

<template>
  <template v-for="node in nodes" :key="node.id">
    <button
      v-if="node.kind === 'folder'"
      type="button"
      class="af-board__node af-board__node--folder"
      :class="{ 'af-board__node--compact': compact }"
      :style="{ paddingLeft: `${10 + (depth || 0) * 14}px` }"
      role="treeitem"
      :aria-expanded="expanded.has(node.id)"
      @click="$emit('toggle-dir', node.id)"
    >
      <el-icon :size="14">
        <FolderOpened v-if="expanded.has(node.id)" />
        <Folder v-else />
      </el-icon>
      <span class="af-board__node-name">{{ node.name }}</span>
    </button>
    <BoardTreeNodes
      v-if="node.kind === 'folder' && expanded.has(node.id)"
      :nodes="node.children"
      :expanded="expanded"
      :selected-id="selectedId"
      :depth="(depth || 0) + 1"
      :compact="compact"
      @toggle-dir="$emit('toggle-dir', $event)"
      @open-file="$emit('open-file', $event)"
    />
    <button
      v-else-if="node.kind === 'file'"
      type="button"
      class="af-board__node af-board__node--file"
      :class="{
        'af-board__node--active': selectedId === node.id,
        'af-board__node--compact': compact,
      }"
      :style="{ paddingLeft: `${10 + (depth || 0) * 14}px` }"
      role="treeitem"
      @click="$emit('open-file', node.id)"
    >
      <el-icon :size="14"><Document /></el-icon>
      <span class="af-board__node-name">{{ node.name }}</span>
      <span class="af-board__node-ver">v{{ node.artifact.version }}</span>
    </button>
  </template>
</template>
