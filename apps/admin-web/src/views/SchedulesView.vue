<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search, Timer } from '@element-plus/icons-vue'
import {
  api,
  type Agent,
  type Schedule,
  type ScheduleStats,
} from '../api/client'
import PageHeader from '../components/PageHeader.vue'

const router = useRouter()
const agents = ref<Agent[]>([])
const schedules = ref<Schedule[]>([])
const stats = ref<ScheduleStats | null>(null)
const loading = ref(false)

const filters = reactive({
  agentId: '',
  userKey: '',
  status: '' as '' | 'active' | 'paused' | 'completed' | 'cancelled' | 'failed',
})

async function loadAgents() {
  try {
    agents.value = await api.listAgents()
    if (!filters.agentId && agents.value.length) {
      filters.agentId = agents.value[0].id
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function refresh() {
  if (!filters.agentId) {
    schedules.value = []
    stats.value = null
    return
  }
  loading.value = true
  stats.value = null
  try {
    const [list, usage] = await Promise.all([
      api.listSchedules(filters.agentId, {
        status: filters.status || undefined,
        user_key: filters.userKey.trim() || undefined,
        limit: 200,
      }),
      api.scheduleStats(filters.agentId, filters.userKey.trim() || undefined),
    ])
    schedules.value = list
    stats.value = usage
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function onSearch() {
  void refresh()
}

function statusType(status: string) {
  if (status === 'active') return 'success'
  if (status === 'paused') return 'warning'
  if (status === 'failed') return 'danger'
  if (status === 'cancelled') return 'info'
  return ''
}

function kindLabel(row: Schedule) {
  if (row.schedule_kind === 'at') return `at ${row.run_at || '-'}`.trim()
  if (row.schedule_kind === 'every') {
    const ms = row.every_ms || 0
    if (ms >= 3600_000 && ms % 3600_000 === 0) return `every ${ms / 3600_000}h`
    if (ms >= 60_000 && ms % 60_000 === 0) return `every ${ms / 60_000}m`
    return `every ${ms}ms`
  }
  if (row.schedule_kind === 'cron') return `cron ${row.cron_expr || '-'}`
  return row.schedule_kind || '-'
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function openSession(sessionId?: string | null, agentId?: string) {
  if (!sessionId) return
  void router.push({
    name: 'sessions',
    query: {
      session_id: sessionId,
      agent_id: agentId || filters.agentId || undefined,
    },
  })
}

async function pause(row: Schedule) {
  try {
    await api.pauseSchedule(row.id)
    ElMessage.success('已暂停')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function resume(row: Schedule) {
  try {
    await api.resumeSchedule(row.id)
    ElMessage.success('已恢复')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function cancel(row: Schedule) {
  try {
    await ElMessageBox.confirm(`取消定时任务「${row.name || row.id}」？`, '确认', {
      type: 'warning',
    })
    await api.cancelSchedule(row.id)
    ElMessage.success('已取消')
    await refresh()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function remove(row: Schedule) {
  try {
    await ElMessageBox.confirm(
      `永久删除定时任务「${row.name || row.id}」？此操作不可恢复。`,
      '删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await api.deleteSchedule(row.id)
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
      title="定时任务"
      description="按 Agent 查看调度 Job；可暂停 / 恢复 / 取消，并跳转到最近开火的会话"
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
            placeholder="选择智能体"
            style="width: 220px"
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
          <el-input
            v-model="filters.userKey"
            clearable
            placeholder="可选"
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="filters.status"
            clearable
            placeholder="全部"
            style="width: 140px"
            @change="onSearch"
          >
            <el-option label="active" value="active" />
            <el-option label="paused" value="paused" />
            <el-option label="completed" value="completed" />
            <el-option label="cancelled" value="cancelled" />
            <el-option label="failed" value="failed" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="onSearch">查询</el-button>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="stats"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
        :title="`配额 · 活跃 ${stats.active}/${stats.max_active_jobs} · 暂停 ${stats.paused} · 失败 ${stats.failed} · 完成 ${stats.completed} · 取消 ${stats.cancelled} · 1h 内到期 ${stats.due_soon} · 合计 ${stats.total}`"
      />

      <el-table v-loading="loading" :data="schedules" stripe empty-text="暂无定时任务">
        <el-table-column label="名称" min-width="140">
          <template #default="{ row }">
            <div class="sched-name">
              <el-icon><Timer /></el-icon>
              <span>{{ row.name || row.id.slice(0, 8) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="调度" min-width="160">
          <template #default="{ row }">
            <div>{{ kindLabel(row) }}</div>
            <div class="muted">{{ row.timezone }}</div>
          </template>
        </el-table-column>
        <el-table-column label="目标 / 模式" min-width="150">
          <template #default="{ row }">
            <div>{{ row.session_target }}</div>
            <div class="muted">{{ row.run_mode }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="plain">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="下次 / 上次" min-width="170">
          <template #default="{ row }">
            <div>下次 {{ formatTime(row.next_run_at) }}</div>
            <div class="muted">上次 {{ formatTime(row.last_run_at) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="载荷" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.payload_text || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="最近会话" width="120">
          <template #default="{ row }">
            <el-button
              v-if="row.last_fired_session_id"
              link
              type="primary"
              @click="openSession(row.last_fired_session_id, row.agent_id)"
            >
              打开
            </el-button>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.last_error" class="err">{{ row.last_error }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'active'"
              link
              type="warning"
              @click="pause(row)"
            >
              暂停
            </el-button>
            <el-button
              v-if="row.status === 'paused'"
              link
              type="success"
              @click="resume(row)"
            >
              恢复
            </el-button>
            <el-button
              v-if="row.status === 'active' || row.status === 'paused'"
              link
              type="danger"
              @click="cancel(row)"
            >
              取消
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.sched-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.err {
  color: var(--el-color-danger);
  font-size: 12px;
}
</style>
