<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, type ModelPreset, type ModelProfile, type RemoteModelInfo } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import { useClientPager } from '../composables/useClientPager'

const models = ref<ModelProfile[]>([])
const { pager, pagedRows: pagedModels, onPageSizeChange } = useClientPager(models)
const presets = ref<ModelPreset[]>([])
const remoteModels = ref<RemoteModelInfo[]>([])
const discoverNote = ref('')
const loading = ref(false)
const discovering = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const testingId = ref('')
const editing = ref<ModelProfile | null>(null)

const form = reactive({
  name: '',
  provider: 'deepseek',
  base_url: 'https://api.deepseek.com/v1',
  api_key: '',
  model_name: 'deepseek-chat',
  temperature: 0.3,
  context_window_tokens: 128000,
  reserve_output_tokens: 8192,
  input_price_per_1m: 0,
  output_price_per_1m: 0,
  is_default: false,
  enabled: true,
  remark: '',
})

const modelOptions = computed(() => {
  const map = new Map<string, { id: string; context_window_tokens?: number | null }>()
  for (const item of remoteModels.value) {
    map.set(item.id, item)
  }
  if (form.model_name.trim() && !map.has(form.model_name.trim())) {
    map.set(form.model_name.trim(), {
      id: form.model_name.trim(),
      context_window_tokens: form.context_window_tokens,
    })
  }
  return Array.from(map.values())
})

async function refresh() {
  loading.value = true
  try {
    const [list, presetList] = await Promise.all([api.listModels(), api.listModelPresets()])
    models.value = list
    presets.value = presetList
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function applyPreset(provider: string) {
  const preset = presets.value.find((item) => item.provider === provider)
  if (!preset) return
  form.provider = preset.provider
  form.base_url = preset.base_url
  form.model_name = preset.model_name
  form.context_window_tokens = preset.context_window_tokens || 128000
  form.reserve_output_tokens = preset.reserve_output_tokens || 8192
  if (!form.name) {
    form.name = `${preset.label}-${preset.model_name}`
  }
  remoteModels.value = []
  discoverNote.value = ''
}

function openCreate() {
  editing.value = null
  form.name = ''
  form.provider = 'deepseek'
  form.base_url = 'https://api.deepseek.com/v1'
  form.api_key = ''
  form.model_name = 'deepseek-chat'
  form.temperature = 0.3
  form.context_window_tokens = 128000
  form.reserve_output_tokens = 8192
  form.input_price_per_1m = 0
  form.output_price_per_1m = 0
  form.is_default = models.value.length === 0
  form.enabled = true
  form.remark = ''
  remoteModels.value = []
  discoverNote.value = ''
  dialogVisible.value = true
  const preset = presets.value.find((item) => item.provider === 'deepseek')
  if (preset) applyPreset('deepseek')
}

function openEdit(row: ModelProfile) {
  editing.value = row
  form.name = row.name
  form.provider = row.provider
  form.base_url = row.base_url
  form.api_key = ''
  form.model_name = row.model_name
  form.temperature = row.temperature ?? 0.3
  form.context_window_tokens = row.context_window_tokens ?? 128000
  form.reserve_output_tokens = row.reserve_output_tokens ?? 8192
  form.input_price_per_1m = row.input_price_per_1m ?? 0
  form.output_price_per_1m = row.output_price_per_1m ?? 0
  form.is_default = row.is_default
  form.enabled = row.enabled
  form.remark = row.remark || ''
  remoteModels.value = []
  discoverNote.value = ''
  dialogVisible.value = true
}

function onModelNameChange(value: string) {
  form.model_name = value
  const hit = remoteModels.value.find((item) => item.id === value)
  if (hit?.context_window_tokens && hit.context_window_tokens > 0) {
    form.context_window_tokens = hit.context_window_tokens
    ElMessage.success(`已带入上下文窗口：${hit.context_window_tokens}`)
  }
}

async function discoverRemoteModels() {
  if (!form.base_url.trim()) {
    ElMessage.warning('请先填写接口地址')
    return
  }
  if (!editing.value && !form.api_key.trim()) {
    ElMessage.warning('请先填写 API Key，再拉取模型列表')
    return
  }
  discovering.value = true
  discoverNote.value = ''
  try {
    const result = await api.discoverModels({
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim() || undefined,
      profile_id: editing.value?.id,
    })
    remoteModels.value = result.models || []
    discoverNote.value = result.note || ''
    if (!remoteModels.value.length) {
      ElMessage.warning(result.note || '未拉到模型列表')
      return
    }
    ElMessage.success(`已拉取 ${remoteModels.value.length} 个模型`)
    const current = remoteModels.value.find((item) => item.id === form.model_name)
    if (current?.context_window_tokens) {
      form.context_window_tokens = current.context_window_tokens
    }
  } catch (err) {
    remoteModels.value = []
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    discovering.value = false
  }
}

async function save() {
  if (!form.name.trim() || !form.base_url.trim() || !form.model_name.trim()) {
    ElMessage.warning('请填写名称、接口地址和模型名')
    return
  }
  if (form.context_window_tokens < 1024) {
    ElMessage.warning('上下文窗口至少 1024')
    return
  }
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      name: form.name.trim(),
      provider: form.provider,
      base_url: form.base_url.trim(),
      model_name: form.model_name.trim(),
      temperature: form.temperature,
      context_window_tokens: form.context_window_tokens,
      reserve_output_tokens: form.reserve_output_tokens,
      input_price_per_1m: form.input_price_per_1m,
      output_price_per_1m: form.output_price_per_1m,
      is_default: form.is_default,
      enabled: form.enabled,
      remark: form.remark.trim() || null,
    }
    if (editing.value) {
      if (form.api_key.trim()) body.api_key = form.api_key.trim()
      await api.updateModel(editing.value.id, body)
      ElMessage.success('保存成功')
    } else {
      body.api_key = form.api_key.trim()
      await api.createModel(body)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    saving.value = false
  }
}

async function remove(row: ModelProfile) {
  try {
    await ElMessageBox.confirm(`确认删除模型「${row.name}」吗？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await api.deleteModel(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function setDefault(row: ModelProfile) {
  try {
    await api.updateModel(row.id, { is_default: true, enabled: true })
    ElMessage.success(`已将「${row.name}」设为默认模型`)
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function testModel(row: ModelProfile) {
  testingId.value = row.id
  try {
    const result = await api.testModel(row.id)
    ElMessage.success(`连通成功：${result.message || 'ok'}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    testingId.value = ''
  }
}

function formatPrice(value?: number | null) {
  const n = Number(value || 0)
  if (!n) return '—'
  return n.toFixed(n >= 1 ? 2 : 4)
}

onMounted(refresh)
</script>

<template>
  <div>
    <PageHeader
      title="模型管理"
      description="配置 DeepSeek、智谱 GLM、OpenAI 等 OpenAI 兼容模型与 API Key"
    >
      <template #actions>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增模型</el-button>
      </template>
    </PageHeader>

    <el-card class="page-card" shadow="never">
      <el-alert
        title="智能体可绑定模型配置；未绑定时将使用默认模型。Key 仅服务端保存，列表中脱敏展示。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-table v-loading="loading" :data="pagedModels" stripe empty-text="暂无模型，请先新增">
        <el-table-column prop="name" label="名称" min-width="140">
          <template #default="{ row }">
            <div style="font-weight: 600">{{ row.name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="厂商" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.provider }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型名" min-width="140" />
        <el-table-column label="上下文" width="110">
          <template #default="{ row }">
            {{ row.context_window_tokens ?? 128000 }}
          </template>
        </el-table-column>
        <el-table-column label="预留输出" width="100">
          <template #default="{ row }">
            {{ row.reserve_output_tokens ?? 8192 }}
          </template>
        </el-table-column>
        <el-table-column prop="temperature" label="温度" width="90" />
        <el-table-column label="输入单价" width="110">
          <template #default="{ row }">
            {{ formatPrice(row.input_price_per_1m) }}
          </template>
        </el-table-column>
        <el-table-column label="输出单价" width="110">
          <template #default="{ row }">
            {{ formatPrice(row.output_price_per_1m) }}
          </template>
        </el-table-column>
        <el-table-column label="接口地址" min-width="220">
          <template #default="{ row }">
            <span class="mono">{{ row.base_url }}</span>
          </template>
        </el-table-column>
        <el-table-column label="API Key" min-width="140">
          <template #default="{ row }">
            <span class="mono">{{ row.has_api_key ? row.api_key_masked : '未配置' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-space>
              <el-tag v-if="row.is_default" type="success" size="small" effect="light">默认</el-tag>
              <el-tag :type="row.enabled ? 'primary' : 'info'" size="small" effect="light">
                {{ row.enabled ? '启用' : '停用' }}
              </el-tag>
            </el-space>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button
                link
                type="primary"
                :loading="testingId === row.id"
                @click="testModel(row)"
              >
                测试
              </el-button>
              <el-button link type="primary" :disabled="row.is_default" @click="setDefault(row)">
                设默认
              </el-button>
              <el-button link type="danger" @click="remove(row)">删除</el-button>
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
      v-model="dialogVisible"
      :title="editing ? '编辑模型' : '新增模型'"
      width="680px"
      destroy-on-close
    >
      <el-form label-width="120px">
        <el-form-item label="快捷模板">
          <el-select
            :model-value="form.provider"
            style="width: 100%"
            @change="(val: string) => applyPreset(val)"
          >
            <el-option
              v-for="preset in presets"
              :key="preset.provider"
              :label="preset.label"
              :value="preset.provider"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：DeepSeek 正式环境" />
        </el-form-item>
        <el-form-item label="厂商">
          <el-input v-model="form.provider" placeholder="deepseek / zhipu / openai / custom" />
        </el-form-item>
        <el-form-item label="接口地址" required>
          <el-input v-model="form.base_url" placeholder="https://api.xxx.com/v1" />
        </el-form-item>
        <el-form-item :label="editing ? 'API Key' : 'API Key'" :required="!editing">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="editing ? '留空表示不修改原 Key' : '请输入 API Key'"
          />
        </el-form-item>
        <el-form-item label="模型名" required>
          <div style="display: flex; gap: 8px; width: 100%">
            <el-select
              :model-value="form.model_name"
              filterable
              allow-create
              default-first-option
              style="flex: 1"
              placeholder="选择或输入模型名"
              @change="onModelNameChange"
            >
              <el-option
                v-for="item in modelOptions"
                :key="item.id"
                :label="
                  item.context_window_tokens
                    ? `${item.id} · ctx ${item.context_window_tokens}`
                    : item.id
                "
                :value="item.id"
              />
            </el-select>
            <el-button :icon="Refresh" :loading="discovering" @click="discoverRemoteModels">
              拉取列表
            </el-button>
          </div>
          <div v-if="discoverNote" style="margin-top: 6px; color: #909399; font-size: 12px">
            {{ discoverNote }}
          </div>
        </el-form-item>
        <el-form-item label="上下文窗口" required>
          <el-input-number
            v-model="form.context_window_tokens"
            :min="1024"
            :max="2000000"
            :step="1024"
          />
          <span style="margin-left: 8px; color: #909399; font-size: 12px">tokens</span>
        </el-form-item>
        <el-form-item label="预留输出">
          <el-input-number
            v-model="form.reserve_output_tokens"
            :min="256"
            :max="200000"
            :step="256"
          />
          <span style="margin-left: 8px; color: #909399; font-size: 12px">tokens</span>
        </el-form-item>
        <el-form-item label="温度">
          <div>
            <el-input-number
              v-model="form.temperature"
              :min="0"
              :max="2"
              :step="0.1"
              :precision="2"
            />
            <div style="margin-top: 6px; color: #909399; font-size: 12px; line-height: 1.5">
              控制回答随机性：越低越稳、越可复现（工具调用/客服建议 0.1–0.4）；越高越发散有创意（文案可 0.7–1.0）。一般用
              0.3。
            </div>
          </div>
        </el-form-item>
        <el-form-item label="输入单价">
          <el-input-number
            v-model="form.input_price_per_1m"
            :min="0"
            :max="100000"
            :step="0.1"
            :precision="4"
          />
          <span style="margin-left: 8px; color: #909399; font-size: 12px">元 / 百万 tokens</span>
        </el-form-item>
        <el-form-item label="输出单价">
          <el-input-number
            v-model="form.output_price_per_1m"
            :min="0"
            :max="100000"
            :step="0.1"
            :precision="4"
          />
          <span style="margin-left: 8px; color: #909399; font-size: 12px">元 / 百万 tokens</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" placeholder="可选" />
        </el-form-item>
        <el-form-item label="选项">
          <el-space>
            <el-switch v-model="form.enabled" active-text="启用" />
            <el-switch v-model="form.is_default" active-text="设为默认" />
          </el-space>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
