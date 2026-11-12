<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, View } from '@element-plus/icons-vue'
import {
  ChatTurnItem,
  fetchTimeline,
  timelineToTurns,
  type ChatTurn,
} from '@agent-factory/chat-ui'
import '@agent-factory/chat-ui/style.css'
import { api, type Session } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import { API_BASE, API_KEY } from '../env'

type PromptSnapshot = {
  id: string
  run_id: string
  created_at: string
  agent_system_prompt: string
  memory_block: string
  context_summary: string
  message_count: number
  estimated_tokens_after?: number | null
  layers_applied: string[]
}

type LlmDebug = {
  session_id: string
  agent_system_prompt: string
  memory_block: string
  context_summary: string
  messages: Array<{ role: string; content?: string | null }>
  prompt_snapshots: PromptSnapshot[]
  note?: string
}

type DetailTab = 'prompt' | 'chat'

const sessions = ref<Session[]>([])
const turns = ref<ChatTurn[]>([])
const selected = ref<Session | null>(null)
const loading = ref(false)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detailTab = ref<DetailTab>('prompt')
const promptLoading = ref(false)
const promptDebug = ref<LlmDebug | null>(null)
const selectedSnapshotId = ref('')
const pager = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
})

async function refresh() {
  loading.value = true
  try {
    const result = await api.listSessions({
      page: pager.page,
      pageSize: pager.pageSize,
    })
    sessions.value = result.items
    pager.total = result.total
    pager.page = result.page
    pager.pageSize = result.page_size
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

async function loadPromptDebug(sessionId: string) {
  promptLoading.value = true
  try {
    promptDebug.value = await api.debugLlmMessages(sessionId)
    selectedSnapshotId.value = promptDebug.value.prompt_snapshots[0]?.id || ''
  } catch (err) {
    promptDebug.value = null
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    promptLoading.value = false
  }
}

async function loadTurns(sessionId: string) {
  detailLoading.value = true
  try {
    const timeline = await fetchTimeline(API_BASE, sessionId, API_KEY || undefined)
    turns.value = timelineToTurns(timeline.runs || [])
  } catch (err) {
    turns.value = []
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    detailLoading.value = false
  }
}

async function openSession(session: Session) {
  selected.value = session
  detailVisible.value = true
  detailTab.value = 'prompt'
  turns.value = []
  promptDebug.value = null
  selectedSnapshotId.value = ''
  void loadTurns(session.id)
  void loadPromptDebug(session.id)
}

function selectedSnapshot(): PromptSnapshot | null {
  const list = promptDebug.value?.prompt_snapshots || []
  return list.find((s) => s.id === selectedSnapshotId.value) || list[0] || null
}

function toggleProcess(turn: ChatTurn) {
  turn.processExpanded = !turn.processExpanded
}

watch(detailTab, (tab) => {
  if (!selected.value) return
  if (tab === 'prompt' && !promptDebug.value && !promptLoading.value) {
    void loadPromptDebug(selected.value.id)
  }
  if (tab === 'chat' && !turns.value.length && !detailLoading.value) {
    void loadTurns(selected.value.id)
  }
})

onMounted(refresh)
</script>

<template>
  <div>
    <PageHeader
      title="会话记录"
      description="查看会话系统提示词与对话详情"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <el-card class="page-card" shadow="never">
      <el-table v-loading="loading" :data="sessions" stripe empty-text="暂无会话">
        <el-table-column prop="title" label="标题" min-width="160">
          <template #default="{ row }">
            <div style="font-weight: 600">{{ row.title || '未命名会话' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="会话 ID" min-width="260">
          <template #default="{ row }">
            <span class="mono">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="智能体 ID" min-width="260">
          <template #default="{ row }">
            <span class="mono">{{ row.agent_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="用户 Key" min-width="140">
          <template #default="{ row }">
            <span class="mono">{{ row.user_key || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="180">
          <template #default="{ row }">
            {{ new Date(row.updated_at).toLocaleString('zh-CN') }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="View" @click="openSession(row)">查看</el-button>
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

    <el-drawer
      v-model="detailVisible"
      size="820px"
      class="session-drawer"
      :title="selected ? `会话 · ${selected.title || '未命名'}` : '会话详情'"
    >
      <template v-if="selected">
        <div class="session-meta">
          <div class="session-meta__item">
            <span class="session-meta__label">会话</span>
            <span class="mono">{{ selected.id.slice(0, 8) }}…</span>
          </div>
          <div class="session-meta__item">
            <span class="session-meta__label">用户</span>
            <span class="mono">{{ selected.user_key || '-' }}</span>
          </div>
          <div class="session-meta__item">
            <span class="session-meta__label">语言</span>
            <span class="mono">{{ selected.locale || 'zh' }}</span>
          </div>
        </div>

        <el-tabs v-model="detailTab" class="session-tabs">
          <el-tab-pane label="系统提示词" name="prompt">
            <div v-loading="promptLoading" class="prompt-pane">
              <section class="prompt-card">
                <div class="prompt-card__title">会话实例化系统提示词</div>
                <div class="prompt-card__desc">
                  创建会话时由 Agent 模板渲染得到；旧会话可能为空并回退到 Agent 模板。
                </div>
                <pre class="prompt-block prompt-block--tall">{{
                  selected.system_prompt || '（旧会话无快照，运行时回退到 Agent 模板）'
                }}</pre>
              </section>

              <section class="prompt-card">
                <div class="prompt-card__title">提示词变量</div>
                <pre class="prompt-block">{{
                  selected.prompt_vars && Object.keys(selected.prompt_vars).length
                    ? JSON.stringify(selected.prompt_vars, null, 2)
                    : '（无）'
                }}</pre>
              </section>

              <template v-if="promptDebug">
                <el-alert
                  :title="promptDebug.note || '下方为实时重算投影，以及历史每次调模型时的前缀快照。'"
                  type="info"
                  show-icon
                  :closable="false"
                  style="margin-bottom: 12px"
                />

                <section class="prompt-card">
                  <div class="prompt-card__title">当前重算 · Agent System（含 Skills）</div>
                  <pre class="prompt-block prompt-block--tall">{{
                    promptDebug.agent_system_prompt || '（空）'
                  }}</pre>
                  <div class="prompt-card__title" style="margin-top: 14px">Memory Block</div>
                  <pre class="prompt-block">{{ promptDebug.memory_block || '（无）' }}</pre>
                  <div class="prompt-card__title" style="margin-top: 14px">Context Summary</div>
                  <pre class="prompt-block">{{ promptDebug.context_summary || '（无）' }}</pre>
                </section>

                <section class="prompt-card">
                  <div class="prompt-card__title">历史快照（每次调模型）</div>
                  <el-empty
                    v-if="!promptDebug.prompt_snapshots.length"
                    description="尚无快照（新对话产生后会出现）"
                  />
                  <template v-else>
                    <el-select
                      v-model="selectedSnapshotId"
                      style="width: 100%; margin-bottom: 12px"
                      placeholder="选择快照"
                    >
                      <el-option
                        v-for="snap in promptDebug.prompt_snapshots"
                        :key="snap.id"
                        :label="`${new Date(snap.created_at).toLocaleString('zh-CN')} · run ${snap.run_id.slice(0, 8)}… · msgs ${snap.message_count}`"
                        :value="snap.id"
                      />
                    </el-select>
                    <template v-if="selectedSnapshot()">
                      <div class="prompt-card__subtitle">当时 Agent System</div>
                      <pre class="prompt-block prompt-block--tall">{{
                        selectedSnapshot()?.agent_system_prompt || '（空）'
                      }}</pre>
                      <div class="prompt-card__subtitle">当时 Memory</div>
                      <pre class="prompt-block">{{ selectedSnapshot()?.memory_block || '（无）' }}</pre>
                      <div class="prompt-card__subtitle">当时 Summary</div>
                      <pre class="prompt-block">{{
                        selectedSnapshot()?.context_summary || '（无）'
                      }}</pre>
                    </template>
                  </template>
                </section>
              </template>
            </div>
          </el-tab-pane>

          <el-tab-pane label="会话详情" name="chat">
            <div v-loading="detailLoading" class="chat-pane">
              <el-empty
                v-if="!detailLoading && !turns.length"
                description="暂无对话回合"
              />
              <div v-else class="af-chat af-chat--session-preview">
                <div class="session-turns">
                  <ChatTurnItem
                    v-for="turn in turns"
                    :key="turn.id"
                    :turn="turn"
                    @toggle-process="toggleProcess(turn)"
                  />
                </div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.session-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 8px;
  padding: 10px 12px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid var(--af-border);
}

.session-meta__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--af-text);
}

.session-meta__label {
  color: var(--af-text-muted);
}

.session-tabs {
  height: calc(100vh - 180px);
}

.session-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}

.session-tabs :deep(.el-tabs__content) {
  height: calc(100% - 54px);
  overflow: auto;
}

.session-tabs :deep(.el-tab-pane) {
  height: 100%;
}

.prompt-pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 24px;
}

.prompt-card {
  border: 1px solid var(--af-border);
  border-radius: 12px;
  padding: 14px;
  background: #fff;
}

.prompt-card__title {
  font-size: 13px;
  font-weight: 650;
  color: var(--af-text);
  margin-bottom: 6px;
}

.prompt-card__subtitle {
  font-size: 12px;
  font-weight: 600;
  color: var(--af-text-secondary);
  margin: 12px 0 6px;
}

.prompt-card__desc {
  font-size: 12px;
  color: var(--af-text-muted);
  margin-bottom: 10px;
  line-height: 1.5;
}

.prompt-block {
  white-space: pre-wrap;
  word-break: break-word;
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 12px 14px;
  font-size: 12px;
  line-height: 1.55;
  max-height: 220px;
  overflow: auto;
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.prompt-block--tall {
  max-height: 360px;
}

.chat-pane {
  min-height: 320px;
  padding-bottom: 24px;
}

.session-turns {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 720px;
  margin: 0 auto;
  padding: 8px 8px 28px;
}
</style>

<style>
/* Preview shell: reuse chat-ui tokens without full-page chat chrome. */
.af-chat.af-chat--session-preview {
  height: auto;
  min-height: 0;
  background: transparent;
}
</style>
