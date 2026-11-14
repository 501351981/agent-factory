<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Key, Plus } from '@element-plus/icons-vue'
import { api, type Agent, type ModelProfile } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import { useClientPager } from '../composables/useClientPager'
import { CHAT_WEB_ORIGIN } from '../env'

const router = useRouter()

const agents = ref<Agent[]>([])
const modelProfiles = ref<ModelProfile[]>([])
const { pager, pagedRows: pagedAgents, onPageSizeChange } = useClientPager(agents)
const loading = ref(false)
const creating = ref(false)
const createVisible = ref(false)

const embedVisible = ref(false)
const embedSnippet = ref('')
const embedAgentId = ref('')

const credVisible = ref(false)
const credBusy = ref(false)
const credAccessKey = ref('')
const credPlainSecret = ref('')

const createForm = reactive({
  name: '',
  model_profile_id: '' as string,
})

async function refresh() {
  loading.value = true
  try {
    const [agentList, modelList] = await Promise.all([api.listAgents(), api.listModels()])
    agents.value = agentList
    modelProfiles.value = modelList.filter((item) => item.enabled)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  const defaultModel = modelProfiles.value.find((item) => item.is_default)
  createForm.name = ''
  createForm.model_profile_id = defaultModel?.id || modelProfiles.value[0]?.id || ''
  createVisible.value = true
}

function openEdit(agent: Agent) {
  router.push(`/agents/${agent.id}`)
}

function buildEmbed(agentId: string) {
  return [
    '// 1) 业务后端用该 Agent 的 AK/SK 换短时 token（可绑定 user_key）：',
    '// POST /api/auth/embed-token',
    '// Headers: X-Access-Key, X-Secret-Key',
    '// Body: { "user_key": "demo-user", "ttl_seconds": 3600 }',
    '// 2) 将返回的 embed_token 拼进 iframe（不要把 SK 放进浏览器）：',
    `<iframe`,
    `  src="${CHAT_WEB_ORIGIN}/embed?agentId=${encodeURIComponent(agentId)}&token=EMBED_TOKEN&locale=zh&userId=demo-user-001&deviceId=demo-mac"`,
    `  style="width:100%;height:640px;border:0;border-radius:8px"`,
    `  allow="clipboard-write"`,
    `></iframe>`,
  ].join('\n')
}

async function createAgent() {
  const name = createForm.name.trim()
  if (!name) {
    ElMessage.warning('请填写智能体名称')
    return
  }
  creating.value = true
  try {
    const created = await api.createAgent({
      name,
      model_profile_id: createForm.model_profile_id || null,
      system_prompt: '你是一个有用的助手。',
      system_prompts: { zh: '你是一个有用的助手。' },
      tools: [],
      skills: [],
      welcome_title: '有什么可以帮忙的？',
      suggested_prompts: [],
      ui_theme: {},
    })
    createVisible.value = false
    ElMessage.success('已创建，进入编辑页继续配置')
    router.push(`/agents/${created.id}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    creating.value = false
  }
}

async function rotateCredentials(agent: Agent) {
  try {
    await ElMessageBox.confirm(
      agent.has_secret_key
        ? '轮换后旧 Secret Key 立即失效，新 SK 只会展示一次。确认继续？'
        : '将为该智能体生成 Access Key / Secret Key。SK 只会展示一次。确认继续？',
      '生成 / 轮换凭证',
      { type: 'warning', confirmButtonText: '生成', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  credBusy.value = true
  try {
    const cred = await api.rotateAgentCredentials(agent.id)
    credAccessKey.value = cred.access_key
    credPlainSecret.value = cred.secret_key
    credVisible.value = true
    ElMessage.success('凭证已生成，请立即保存 Secret Key')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    credBusy.value = false
  }
}

async function revokeCredentials(agent: Agent) {
  try {
    await ElMessageBox.confirm('作废后该 Agent 的 AK/SK 将无法再换取 embed token。确认？', '作废凭证', {
      type: 'warning',
      confirmButtonText: '作废',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await api.revokeAgentCredentials(agent.id)
    ElMessage.success('已作废凭证')
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function copyEmbed(agentId: string) {
  embedAgentId.value = agentId
  embedSnippet.value = buildEmbed(agentId)
  embedVisible.value = true
  try {
    await navigator.clipboard.writeText(embedSnippet.value)
    ElMessage.success('嵌入代码已复制到剪贴板')
  } catch {
    ElMessage.warning('已生成嵌入代码，请手动复制')
  }
}

async function recopyEmbed() {
  if (!embedAgentId.value) return
  await copyEmbed(embedAgentId.value)
}

async function copyCreds() {
  try {
    await navigator.clipboard.writeText(`${credAccessKey.value}\n${credPlainSecret.value}`)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择')
  }
}

async function remove(agent: Agent) {
  try {
    await ElMessageBox.confirm(`确认删除智能体「${agent.name}」吗？此操作不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await api.deleteAgent(agent.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(refresh)
</script>

<template>
  <div>
    <PageHeader
      title="智能体管理"
      description="先创建智能体，再进入编辑页配置提示词、Tools / Skills 与主题"
    >
      <template #actions>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建智能体</el-button>
      </template>
    </PageHeader>

    <el-card class="page-card" shadow="never">
      <el-alert
        title="请先在「模型管理」中配置模型与 Key，再为智能体绑定。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-table v-loading="loading" :data="pagedAgents" stripe empty-text="暂无智能体，请先新建">
        <el-table-column prop="name" label="名称" min-width="140">
          <template #default="{ row }">
            <div style="font-weight: 600">{{ row.name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="ID" min-width="220">
          <template #default="{ row }">
            <span class="mono">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="绑定模型" min-width="150">
          <template #default="{ row }">
            <el-tag effect="plain" type="info" size="small">
              {{ row.model_profile_name || '默认模型' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Tools" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" round>{{ row.tools.length }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Skills" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" type="success" round>{{ (row.skills || []).length }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="凭证" min-width="220">
          <template #default="{ row }">
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
              <span v-if="row.access_key" class="mono">{{ row.access_key }}</span>
              <span v-else class="mono">未生成</span>
              <el-tag v-if="row.has_secret_key" size="small" type="success" effect="light">
                SK 已配置
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="170">
          <template #default="{ row }">
            {{ new Date(row.updated_at).toLocaleString('zh-CN') }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link type="primary" @click="copyEmbed(row.id)">嵌入</el-button>
              <el-dropdown trigger="click">
                <el-button link type="primary">更多</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item :icon="Key" :disabled="credBusy" @click="rotateCredentials(row)">
                      {{ row.has_secret_key ? '轮换 SK' : '生成 AK/SK' }}
                    </el-dropdown-item>
                    <el-dropdown-item
                      v-if="row.has_secret_key"
                      divided
                      @click="revokeCredentials(row)"
                    >
                      作废凭证
                    </el-dropdown-item>
                    <el-dropdown-item divided style="color: var(--el-color-danger)" @click="remove(row)">
                      删除
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
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
          @size-change="onPageSizeChange"
        />
      </div>
    </el-card>

    <el-dialog
      v-model="createVisible"
      title="新建智能体"
      width="480px"
      destroy-on-close
      append-to-body
    >
      <el-form label-width="88px" @submit.prevent>
        <el-form-item label="名称" required>
          <el-input
            v-model="createForm.name"
            placeholder="例如：客服助手"
            maxlength="64"
            show-word-limit
            @keyup.enter="createAgent"
          />
        </el-form-item>
        <el-form-item label="绑定模型">
          <el-select
            v-model="createForm.model_profile_id"
            clearable
            filterable
            placeholder="不选则使用默认模型"
            style="width: 100%"
          >
            <el-option
              v-for="item in modelProfiles"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <div class="hint">创建后进入编辑页，再配置提示词、Tools、Skills 等。</div>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createAgent">创建并编辑</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="embedVisible" title="业务嵌入说明" width="720px">
      <el-alert
        title="token 必须由业务后端用 AK/SK 签发；前端只拿短时 embed_token。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-input :model-value="embedSnippet" type="textarea" :rows="12" readonly />
      <template #footer>
        <el-button type="primary" @click="recopyEmbed">重新复制</el-button>
        <el-button @click="embedVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="credVisible" title="请保存 Secret Key" width="560px" :close-on-click-modal="false">
      <el-alert
        title="Secret Key 只展示这一次，关闭后无法再次查看。"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-form label-width="110px">
        <el-form-item label="Access Key">
          <el-input :model-value="credAccessKey" readonly />
        </el-form-item>
        <el-form-item label="Secret Key">
          <el-input :model-value="credPlainSecret" readonly type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="copyCreds">复制 AK/SK</el-button>
        <el-button @click="credVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.table-actions {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}
.table-actions :deep(.el-button + .el-button),
.table-actions :deep(.el-button + .el-dropdown),
.table-actions :deep(.el-dropdown + .el-button) {
  margin-left: 0;
}
.hint {
  color: var(--af-text-secondary);
  font-size: 12px;
  line-height: 1.5;
  margin-left: 88px;
}
</style>
