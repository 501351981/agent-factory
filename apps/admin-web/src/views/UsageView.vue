<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  api,
  type Agent,
  type LlmUsage,
  type UsageSummary,
  type UsageSummaryBucket,
} from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import { useClientPager } from '../composables/useClientPager'

const loading = ref(false)
const summary = ref<UsageSummary | null>(null)
const recent = ref<LlmUsage[]>([])
const agents = ref<Agent[]>([])

const filters = reactive({
  group_by: 'day' as 'day' | 'model' | 'agent',
  agent_id: '' as string,
  range: [] as string[],
})
const recentPager = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
})

const totals = computed<UsageSummaryBucket | null>(() => summary.value?.totals || null)
const buckets = computed(() => summary.value?.buckets || [])
const { pager: bucketPager, pagedRows: pagedBuckets, onPageSizeChange: onBucketPageSizeChange } =
  useClientPager(buckets, 20)

function formatCost(value?: number | null) {
  const n = Number(value || 0)
  if (!n) return '¥0'
  if (n < 0.01) return `¥${n.toFixed(6)}`
  return `¥${n.toFixed(4)}`
}

function formatTokens(value?: number | null) {
  return Number(value || 0).toLocaleString()
}

function formatTime(value?: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

async function refresh() {
  loading.value = true
  try {
    const [from_time, to_time] = filters.range || []
    const [summaryResult, recentResult, agentList] = await Promise.all([
      api.usageSummary({
        group_by: filters.group_by,
        agent_id: filters.agent_id || undefined,
        from_time: from_time || undefined,
        to_time: to_time || undefined,
      }),
      api.listUsage({
        agent_id: filters.agent_id || undefined,
        page: recentPager.page,
        pageSize: recentPager.pageSize,
      }),
      agents.value.length ? Promise.resolve(agents.value) : api.listAgents(),
    ])
    summary.value = summaryResult
    recent.value = recentResult.items
    recentPager.total = recentResult.total
    recentPager.page = recentResult.page
    recentPager.pageSize = recentResult.page_size
    agents.value = agentList
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function onRecentPageChange() {
  void refresh()
}

function onRecentPageSizeChange() {
  recentPager.page = 1
  void refresh()
}

function onFilterChange() {
  recentPager.page = 1
  bucketPager.page = 1
  void refresh()
}

onMounted(refresh)
</script>

<template>
  <div>
    <PageHeader
      title="用量与花费"
      description="按 LLM 调用统计 tokens（含缓存命中 / 未缓存输入）；费用按调用当时模型单价估算（元）"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <el-card class="page-card" shadow="never">
      <el-form class="filter-bar" inline>
        <el-form-item label="汇总维度">
          <el-radio-group v-model="filters.group_by" @change="onFilterChange">
            <el-radio-button value="day">按日</el-radio-button>
            <el-radio-button value="model">按模型</el-radio-button>
            <el-radio-button value="agent">按智能体</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="智能体">
          <el-select
            v-model="filters.agent_id"
            clearable
            placeholder="全部"
            style="width: 200px"
            @change="onFilterChange"
          >
            <el-option
              v-for="agent in agents"
              :key="agent.id"
              :label="agent.name"
              :value="agent.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="filters.range"
            type="datetimerange"
            value-format="YYYY-MM-DDTHH:mm:ss"
            start-placeholder="开始"
            end-placeholder="结束"
            @change="onFilterChange"
          />
        </el-form-item>
      </el-form>

      <el-alert
        v-if="summary?.note"
        :title="summary.note"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-row :gutter="12" class="stat-grid">
        <el-col :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <el-statistic title="调用次数" :value="totals?.calls || 0" />
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <el-statistic title="输入 tokens" :value="totals?.prompt_tokens || 0" />
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <el-statistic title="缓存命中" :value="totals?.cached_tokens || 0" />
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <el-statistic title="未缓存输入" :value="totals?.uncached_prompt_tokens || 0" />
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <el-statistic title="输出 tokens" :value="totals?.completion_tokens || 0" />
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <div class="stat-cost__label">估算费用</div>
            <div class="stat-cost__value">{{ formatCost(totals?.estimated_cost) }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-table v-loading="loading" :data="pagedBuckets" stripe empty-text="暂无用量数据">
        <el-table-column prop="label" :label="filters.group_by === 'day' ? '日期' : '名称'" min-width="140" />
        <el-table-column prop="calls" label="调用" width="80" />
        <el-table-column label="输入" width="110">
          <template #default="{ row }">{{ formatTokens(row.prompt_tokens) }}</template>
        </el-table-column>
        <el-table-column label="缓存命中" width="110">
          <template #default="{ row }">{{ formatTokens(row.cached_tokens) }}</template>
        </el-table-column>
        <el-table-column label="未缓存输入" width="120">
          <template #default="{ row }">{{ formatTokens(row.uncached_prompt_tokens) }}</template>
        </el-table-column>
        <el-table-column label="输出" width="110">
          <template #default="{ row }">{{ formatTokens(row.completion_tokens) }}</template>
        </el-table-column>
        <el-table-column label="合计 tokens" width="120">
          <template #default="{ row }">{{ formatTokens(row.total_tokens) }}</template>
        </el-table-column>
        <el-table-column label="估算费用" width="110">
          <template #default="{ row }">{{ formatCost(row.estimated_cost) }}</template>
        </el-table-column>
      </el-table>
      <div class="table-pager">
        <el-pagination
          v-model:current-page="bucketPager.page"
          v-model:page-size="bucketPager.pageSize"
          background
          layout="total, sizes, prev, pager, next"
          :total="bucketPager.total"
          :page-sizes="[10, 20, 50, 100]"
          @size-change="onBucketPageSizeChange"
        />
      </div>
    </el-card>

    <el-card class="page-card" shadow="never">
      <template #header>
        <div class="page-card-title">最近调用</div>
      </template>
      <el-table v-loading="loading" :data="recent" stripe empty-text="暂无调用记录">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型" min-width="140" />
        <el-table-column label="输入" width="90">
          <template #default="{ row }">{{ formatTokens(row.prompt_tokens) }}</template>
        </el-table-column>
        <el-table-column label="缓存" width="90">
          <template #default="{ row }">{{ formatTokens(row.cached_tokens) }}</template>
        </el-table-column>
        <el-table-column label="未缓存" width="90">
          <template #default="{ row }">
            {{ formatTokens(row.uncached_prompt_tokens ?? Math.max(0, (row.prompt_tokens || 0) - (row.cached_tokens || 0))) }}
          </template>
        </el-table-column>
        <el-table-column label="输出" width="90">
          <template #default="{ row }">{{ formatTokens(row.completion_tokens) }}</template>
        </el-table-column>
        <el-table-column label="来源" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="row.source === 'api' ? 'success' : 'warning'">
              {{ row.source === 'api' ? '官方' : '估算' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="费用" width="110">
          <template #default="{ row }">{{ formatCost(row.estimated_cost) }}</template>
        </el-table-column>
        <el-table-column prop="session_id" label="会话" min-width="180">
          <template #default="{ row }">
            <span class="mono">{{ row.session_id }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="table-pager">
        <el-pagination
          v-model:current-page="recentPager.page"
          v-model:page-size="recentPager.pageSize"
          background
          layout="total, sizes, prev, pager, next"
          :total="recentPager.total"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="onRecentPageChange"
          @size-change="onRecentPageSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>
