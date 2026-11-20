<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Refresh, View } from '@element-plus/icons-vue'
import {
  ChatTurnItem,
  fetchTimeline,
  timelineToTurns,
  type ChatTurn,
} from '@agent-factory/chat-ui'
import '@agent-factory/chat-ui/style.css'
import {
  api,
  type Agent,
  type BoardProject,
  type ChildRun,
  type Session,
} from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import { API_BASE } from '../env'
import { getAdminToken } from '../auth'

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

type DetailTab = 'prompt' | 'chat' | 'board' | 'children'

const route = useRoute()
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
const includeArchived = ref(false)
const board = ref<BoardProject | null>(null)
const boardLoading = ref(false)
const childRuns = ref<ChildRun[]>([])
const childrenLoading = ref(false)
const agentsById = ref<Record<string, string>>({})
const selectedChild = ref<ChildRun | null>(null)
const childTurns = ref<ChatTurn[]>([])
const childDetailLoading = ref(false)
const childDrawerVisible = ref(false)
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
      includeArchived: includeArchived.value,
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

async function hardDeleteSession(row: Session) {
  try {
    await ElMessageBox.confirm(
      `永久删除会话「${row.title || row.id}」？将删除全部消息与运行记录，不可恢复。`,
      '硬删除会话',
      { type: 'warning', confirmButtonText: '永久删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await api.deleteSession(row.id, true)
    ElMessage.success('已永久删除')
    if (selected.value?.id === row.id) {
      detailVisible.value = false
      selected.value = null
    }
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
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
    const timeline = await fetchTimeline(API_BASE, sessionId, getAdminToken() || undefined)
    turns.value = timelineToTurns(timeline.runs || [])
  } catch (err) {
    turns.value = []
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    detailLoading.value = false
  }
}

async function loadBoard(sessionId: string) {
  boardLoading.value = true
  try {
    board.value = await api.getSessionBoard(sessionId, true)
  } catch (err) {
    board.value = null
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    boardLoading.value = false
  }
}

async function ensureAgentNames() {
  if (Object.keys(agentsById.value).length) return
  try {
    const list = await api.listAgents()
    const map: Record<string, string> = {}
    for (const a of list as Agent[]) map[a.id] = a.name
    agentsById.value = map
  } catch {
    /* ignore — fall back to raw ids */
  }
}

function agentLabel(id: string) {
  return agentsById.value[id] || id.slice(0, 8)
}

function formatDuration(row: ChildRun) {
  if (!row.started_at || !row.finished_at) return row.status === 'running' ? '进行中' : '-'
  const ms = new Date(row.finished_at).getTime() - new Date(row.started_at).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '-'
  const sec = Math.round(ms / 1000)
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m${sec % 60}s`
}

async function loadChildren() {
  if (!selected.value) return
  childrenLoading.value = true
  childRuns.value = []
  selectedChild.value = null
  childTurns.value = []
  try {
    await ensureAgentNames()
    childRuns.value = await api.listSessionDelegations(selected.value.id)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    childrenLoading.value = false
  }
}

async function openChildDetail(row: ChildRun) {
  selectedChild.value = row
  childDrawerVisible.value = true
  childDetailLoading.value = true
  childTurns.value = []
  try {
    // Child sessions are visibility=system; timeline API still works by session id.
    const timeline = await fetchTimeline(
      API_BASE,
      row.session_id,
      getAdminToken() || undefined,
    )
    // Same rendering as the lead session「会话详情」tab; expand process so
    // reasoning / tool_call / tool_result are visible without an extra click.
    childTurns.value = timelineToTurns(timeline.runs || []).map((turn) => ({
      ...turn,
      processExpanded: true,
    }))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    childDetailLoading.value = false
  }
}

async function openSession(session: Session) {
  selected.value = session
  detailVisible.value = true
  detailTab.value = 'prompt'
  turns.value = []
  promptDebug.value = null
  selectedSnapshotId.value = ''
  board.value = null
  childRuns.value = []
  selectedChild.value = null
  childTurns.value = []
  childDrawerVisible.value = false
  void loadTurns(session.id)
  void loadPromptDebug(session.id)
}

async function openSessionFromQuery() {
  const sessionId = String(route.query.session_id || '').trim()
  if (!sessionId) return
  const hit = sessions.value.find((s) => s.id === sessionId)
  const agentId = String(route.query.agent_id || '').trim()
  const target =
    hit ||
    ({
      id: sessionId,
      agent_id: agentId,
      title: sessionId.slice(0, 8),
      created_at: '',
      updated_at: '',
    } satisfies Session)
  await openSession(target)
  detailTab.value = 'chat'
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
  if (tab === 'board' && !board.value && !boardLoading.value) {
    void loadBoard(selected.value.id)
  }
  if (tab === 'children' && !childRuns.value.length && !childrenLoading.value) {
    void loadChildren()
  }
})

onMounted(async () => {
  await refresh()
  await openSessionFromQuery()
})
</script>

<template>
  <div>
    <PageHeader
      title="会话记录"
      description="查看会话系统提示词与对话详情"
    >
      <template #actions>
        <el-checkbox
          v-model="includeArchived"
          label="显示已归档"
          @change="() => { pager.page = 1; void refresh() }"
        />
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
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'archived' ? 'info' : 'success'">
              {{ row.status === 'archived' ? '已归档' : '进行中' }}
            </el-tag>
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
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="View" @click="openSession(row)">查看</el-button>
            <el-button link type="danger" :icon="Delete" @click="hardDeleteSession(row)">
              删除
            </el-button>
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

          <el-tab-pane label="Project Board" name="board">
            <div v-loading="boardLoading" class="prompt-pane">
              <el-empty
                v-if="!boardLoading && (!board || !board.artifacts.length)"
                description="暂无 Board 产物"
              />
              <el-table v-else :data="board?.artifacts || []" stripe>
                <el-table-column prop="path" label="路径" min-width="160" />
                <el-table-column prop="title" label="标题" min-width="120" />
                <el-table-column prop="version" label="版本" width="70" />
                <el-table-column prop="author_agent_id" label="作者 Agent" min-width="160">
                  <template #default="{ row }">
                    <span class="mono">{{ row.author_agent_id || '-' }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="更新" min-width="160">
                  <template #default="{ row }">
                    {{ new Date(row.updated_at).toLocaleString('zh-CN') }}
                  </template>
                </el-table-column>
              </el-table>
              <div v-if="board?.artifacts?.length" style="margin-top: 12px">
                <el-collapse>
                  <el-collapse-item
                    v-for="art in board.artifacts"
                    :key="art.id"
                    :title="`${art.path} (v${art.version})`"
                  >
                    <pre class="prompt-block">{{ art.content || '（空）' }}</pre>
                  </el-collapse-item>
                </el-collapse>
              </div>
            </div>
          </el-tab-pane>

          <el-tab-pane label="子 Run" name="children">
            <div v-loading="childrenLoading" class="prompt-pane">
              <div class="hint" style="margin-bottom: 10px">
                每次委托会建隐藏子 Session。点「查看对话」可像普通会话一样看完整回合（任务、思考、工具、回复）。
              </div>
              <el-empty
                v-if="!childrenLoading && !childRuns.length"
                description="本会话下暂无委托子 Run"
              />
              <el-table v-else :data="childRuns" stripe>
                <el-table-column label="状态" width="110">
                  <template #default="{ row }">
                    <el-tag
                      size="small"
                      :type="
                        row.status === 'completed'
                          ? 'success'
                          : row.status === 'cancelled' || row.status === 'failed'
                            ? 'danger'
                            : 'info'
                      "
                    >
                      {{ row.status }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="Agent" min-width="120">
                  <template #default="{ row }">{{ agentLabel(row.agent_id) }}</template>
                </el-table-column>
                <el-table-column label="耗时" width="90">
                  <template #default="{ row }">{{ formatDuration(row) }}</template>
                </el-table-column>
                <el-table-column prop="user_input" label="任务" min-width="200" show-overflow-tooltip />
                <el-table-column label="结束原因" width="120" show-overflow-tooltip>
                  <template #default="{ row }">
                    {{ row.finish_reason || row.error_message || '-' }}
                  </template>
                </el-table-column>
                <el-table-column label="摘要" min-width="160" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.summary || '-' }}</template>
                </el-table-column>
                <el-table-column label="操作" width="110" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="primary" @click.stop="openChildDetail(row)">
                      查看对话
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-drawer
      v-model="childDrawerVisible"
      size="55%"
      destroy-on-close
      :title="
        selectedChild
          ? `子 Agent 对话 · ${agentLabel(selectedChild.agent_id)}`
          : '子 Agent 对话'
      "
    >
      <template v-if="selectedChild">
        <div class="session-meta">
          <div class="session-meta__item">
            <span class="session-meta__label">状态</span>
            <el-tag size="small">{{ selectedChild.status }}</el-tag>
          </div>
          <div class="session-meta__item">
            <span class="session-meta__label">耗时</span>
            <span>{{ formatDuration(selectedChild) }}</span>
          </div>
          <div class="session-meta__item">
            <span class="session-meta__label">结束</span>
            <span>{{ selectedChild.finish_reason || selectedChild.error_message || '-' }}</span>
          </div>
          <div class="session-meta__item">
            <span class="session-meta__label">子 Session</span>
            <span class="mono">{{ selectedChild.session_id }}</span>
          </div>
          <div class="session-meta__item">
            <span class="session-meta__label">子 Run</span>
            <span class="mono">{{ selectedChild.run_id }}</span>
          </div>
        </div>
        <div v-loading="childDetailLoading" class="chat-pane child-chat-pane">
          <el-empty
            v-if="!childDetailLoading && !childTurns.length"
            description="暂无子对话步骤"
          />
          <div v-else class="af-chat af-chat--session-preview">
            <div class="session-turns">
              <ChatTurnItem
                v-for="turn in childTurns"
                :key="turn.id"
                :turn="turn"
                @toggle-process="toggleProcess(turn)"
              />
            </div>
          </div>
        </div>
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

.child-chat-pane {
  min-height: 60vh;
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
