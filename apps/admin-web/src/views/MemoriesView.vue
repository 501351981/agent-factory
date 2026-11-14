<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { api, type Agent, type Memory, type MemoryUsage } from '../api/client'
import PageHeader from '../components/PageHeader.vue'

const agents = ref<Agent[]>([])
const memories = ref<Memory[]>([])
const usage = ref<MemoryUsage | null>(null)
const loading = ref(false)

const filters = reactive({
  agentId: '',
  userKey: '',
  kind: '' as '' | 'profile' | 'playbook' | 'detail',
})
const pager = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
})

async function loadAgents() {
  try {
    agents.value = await api.listAgents()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function refresh() {
  loading.value = true
  usage.value = null
  try {
    const result = await api.listMemories({
      agent_id: filters.agentId || undefined,
      user_key: filters.userKey.trim() || undefined,
      kind: filters.kind || undefined,
      page: pager.page,
      pageSize: pager.pageSize,
    })
    memories.value = result.items
    pager.total = result.total
    pager.page = result.page
    pager.pageSize = result.page_size
    if (filters.agentId && filters.userKey.trim()) {
      usage.value = await api.memoryUsage(filters.agentId, filters.userKey.trim())
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function onPageChange() {
  void refresh()
}

function onPageSizeChange() {
  pager.page = 1
  void refresh()
}

function onSearch() {
  pager.page = 1
  void refresh()
}

function kindLabel(kind: string) {
  if (kind === 'profile') return '画像'
  if (kind === 'playbook') return '实践目录'
  if (kind === 'detail') return '实践明细'
  return kind || '-'
}

async function removeMemory(row: Memory) {
  try {
    await ElMessageBox.confirm(`删除记忆「${row.key}」？`, '确认', { type: 'warning' })
    await api.deleteMemory(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(async () => {
  await loadAgents()
  await refresh()
})
</script>

<template>
  <div>
    <PageHeader
      title="记忆"
      description="三层记忆 + 配额用量；inactive 为淘汰后的冷目录（仍可读）"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <el-card class="page-card" shadow="never">
      <el-form class="filter-bar" inline>
        <el-form-item label="智能体">
          <el-select
            v-model="filters.agentId"
            clearable
            placeholder="全部"
            style="width: 200px"
            @change="onSearch"
          >
            <el-option
              v-for="agent in agents"
              :key="agent.id"
              :label="agent.name"
              :value="agent.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="用户 Key">
          <el-input v-model="filters.userKey" clearable placeholder="用量需填写" style="width: 160px" />
        </el-form-item>
        <el-form-item label="层级">
          <el-select v-model="filters.kind" clearable placeholder="全部" style="width: 160px">
            <el-option label="画像 profile" value="profile" />
            <el-option label="实践目录 playbook" value="playbook" />
            <el-option label="实践明细 detail" value="detail" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="onSearch">查询</el-button>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="usage"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
        :title="`用量 · profile ${usage.profile_active}/${usage.quotas.max_profile_items ?? '-'} 活跃（冷 ${usage.profile_inactive}）· playbook ${usage.playbook_active}/${usage.quotas.max_playbook_items ?? '-'} 活跃（冷 ${usage.playbook_inactive}）· detail 活跃 ${usage.detail_active} · 约 ${usage.approx_chars} 字`"
      />

      <el-table v-loading="loading" :data="memories" stripe empty-text="暂无记忆">
        <el-table-column label="层级" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="key" label="Key" min-width="120" />
        <el-table-column prop="content" label="内容" min-width="240" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="row.active === false ? 'info' : 'success'">
              {{ row.active === false ? '冷' : '活跃' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="用户 Key" min-width="110">
          <template #default="{ row }">
            <span class="mono">{{ row.user_key || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="160">
          <template #default="{ row }">
            {{ new Date(row.updated_at).toLocaleString('zh-CN') }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeMemory(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="table-pager">
        <el-pagination
          v-model:current-page="pager.page"
          v-model:page-size="pager.pageSize"
          background
          layout="total, sizes, prev, pager, next"
          :total="pager.total"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="onPageChange"
          @size-change="onPageSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>
