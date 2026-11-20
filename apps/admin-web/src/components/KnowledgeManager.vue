<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadUserFile } from 'element-plus'
import { Delete, Edit, Plus, Refresh, Search, View } from '@element-plus/icons-vue'
import { api, type KbDocument } from '../api/client'

/**
 * Knowledge manager for one Agent, rendered inside the Agent editor.
 *
 * 表格形态：个人文档默认展示该 Agent 下全部用户的数据，支持分页；
 * 上传通过弹窗设置目录（默认根目录）；编辑用弹窗，详情用抽屉。
 */
const props = defineProps<{ agentId: string }>()

const scope = ref<'agent' | 'user'>('agent')
const userKey = ref('')
const keyword = ref('')
const prefix = ref('')
const page = ref(1)
const pageSize = ref(20)

const docs = ref<KbDocument[]>([])
const total = ref(0)
const userKeys = ref<string[]>([])
const loading = ref(false)

const uploadVisible = ref(false)
const uploading = ref(false)
const uploadFiles = ref<UploadUserFile[]>([])
const uploadForm = reactive({
  scope: 'agent' as 'agent' | 'user',
  user_key: '',
  dir: '',
  summary: '',
})

const editVisible = ref(false)
const saving = ref(false)
const editing = ref<KbDocument | null>(null)
const editForm = reactive({ title: '', path: '', summary: '' })

const detailVisible = ref(false)
const detail = ref<KbDocument | null>(null)
const detailBody = ref('')
const detailTotalChars = ref(0)
const detailTruncated = ref(false)
const detailLoading = ref(false)

const currentDir = computed(() => prefix.value || '根目录')

function dirOf(path: string): string {
  const index = path.lastIndexOf('/')
  return index < 0 ? '' : path.slice(0, index)
}

function fileName(path: string): string {
  const index = path.lastIndexOf('/')
  return index < 0 ? path : path.slice(index + 1)
}

function sourceLabel(source: string) {
  if (source === 'llm') return '模型生成'
  if (source === 'manual') return '人工填写'
  return '规则抽取'
}

function formatSize(bytes: number) {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function formatTime(value: string) {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

async function load() {
  if (!props.agentId) {
    docs.value = []
    total.value = 0
    return
  }
  loading.value = true
  try {
    const data = await api.listKnowledgeDocuments(props.agentId, {
      scope: scope.value,
      userKey: scope.value === 'user' ? userKey.value || undefined : undefined,
      pathPrefix: prefix.value || undefined,
      keyword: keyword.value.trim() || undefined,
      page: page.value,
      pageSize: pageSize.value,
    })
    docs.value = data.items
    total.value = data.total
    if (scope.value === 'user') userKeys.value = data.user_keys
    // 删除最后一页的最后一条后回退一页
    if (!data.items.length && data.total > 0 && page.value > 1) {
      page.value = Math.max(1, Math.ceil(data.total / pageSize.value))
      await load()
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function reload(resetPage = true) {
  if (resetPage) page.value = 1
  return load()
}

function onScopeChange() {
  userKey.value = ''
  void reload()
}

function onPageChange(value: number) {
  page.value = value
  void load()
}

function onPageSizeChange(value: number) {
  pageSize.value = value
  void reload()
}

function filterByDir(dir: string) {
  prefix.value = dir
  void reload()
}

function clearPrefix() {
  prefix.value = ''
  void reload()
}

function openUpload() {
  uploadForm.scope = scope.value
  uploadForm.user_key = scope.value === 'user' ? userKey.value : ''
  uploadForm.dir = prefix.value
  uploadForm.summary = ''
  uploadFiles.value = []
  uploadVisible.value = true
}

async function submitUpload() {
  const raw = uploadFiles.value[0]?.raw
  if (!raw) {
    ElMessage.warning('请选择要上传的文档')
    return
  }
  if (uploadForm.scope === 'user' && !uploadForm.user_key.trim()) {
    ElMessage.warning('个人文档需要填写 user_key')
    return
  }
  uploading.value = true
  try {
    const result = await api.uploadKnowledgeDocument(props.agentId, raw, {
      scope: uploadForm.scope,
      userKey: uploadForm.scope === 'user' ? uploadForm.user_key.trim() : undefined,
      dir: uploadForm.dir.trim().replace(/^\/+|\/+$/g, ''),
      summary: uploadForm.summary.trim() || undefined,
    })
    ElMessage.success(result.created ? '已上传并生成摘要' : '已覆盖同路径文档')
    for (const warning of result.warnings) ElMessage.warning(warning)
    uploadVisible.value = false
    scope.value = uploadForm.scope
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    uploading.value = false
  }
}

function openEdit(doc: KbDocument) {
  editing.value = doc
  editForm.title = doc.title
  editForm.path = doc.path
  editForm.summary = doc.summary
  editVisible.value = true
}

async function submitEdit() {
  if (!editing.value) return
  saving.value = true
  try {
    await api.updateKnowledgeDocument(props.agentId, editing.value.id, {
      title: editForm.title,
      path: editForm.path,
      summary: editForm.summary,
    })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    saving.value = false
  }
}

async function openDetail(doc: KbDocument) {
  detail.value = doc
  detailBody.value = ''
  detailTotalChars.value = 0
  detailTruncated.value = false
  detailVisible.value = true
  detailLoading.value = true
  try {
    const data = await api.getKnowledgeDocumentContent(props.agentId, doc.id)
    detailBody.value = data.content
    detailTotalChars.value = data.total_chars
    detailTruncated.value = data.truncated
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    detailLoading.value = false
  }
}

async function removeDoc(doc: KbDocument) {
  try {
    await ElMessageBox.confirm(`删除文档「${doc.path}」？`, '删除文档', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await api.deleteKnowledgeDocument(props.agentId, doc.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

watch(
  () => props.agentId,
  () => {
    void reload()
  },
  { immediate: true },
)
</script>

<template>
  <div class="kb-manager">
    <div class="kb-toolbar">
      <el-radio-group v-model="scope" @change="onScopeChange">
        <el-radio-button value="agent">Agent 共享</el-radio-button>
        <el-radio-button value="user">个人文档</el-radio-button>
      </el-radio-group>
      <el-select
        v-if="scope === 'user'"
        v-model="userKey"
        placeholder="全部用户"
        style="width: 200px"
        clearable
        filterable
        allow-create
        default-first-option
        @change="reload()"
      >
        <el-option label="全部用户" value="" />
        <el-option v-for="key in userKeys" :key="key" :label="key" :value="key" />
      </el-select>
      <el-input
        v-model="keyword"
        placeholder="搜索标题 / 路径 / 摘要"
        style="width: 230px"
        clearable
        :prefix-icon="Search"
        @keyup.enter="reload()"
        @clear="reload()"
      />
      <el-tag v-if="prefix" closable type="info" @close="clearPrefix">
        目录：{{ prefix }}
      </el-tag>
      <span class="muted">共 {{ total }} 篇</span>
      <div class="kb-toolbar__actions">
        <el-button size="small" :icon="Refresh" @click="reload(false)">刷新</el-button>
        <el-button size="small" type="primary" :icon="Plus" @click="openUpload">
          上传文档
        </el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="docs" size="small" style="margin-top: 10px">
      <el-table-column label="文档" min-width="220">
        <template #default="{ row }">
          <div class="kb-doc">
            <span class="kb-doc__title">{{ row.title || fileName(row.path) }}</span>
            <span class="muted kb-doc__path">{{ row.path }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="目录" width="150">
        <template #default="{ row }">
          <el-link
            v-if="dirOf(row.path)"
            type="primary"
            :underline="false"
            @click="filterByDir(dirOf(row.path))"
          >
            {{ dirOf(row.path) }}
          </el-link>
          <span v-else class="muted">根目录</span>
        </template>
      </el-table-column>
      <el-table-column v-if="scope === 'user'" label="用户" width="150">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.user_key }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="摘要" min-width="240">
        <template #default="{ row }">
          <el-tooltip :content="row.summary || '（无摘要）'" placement="top">
            <span class="kb-summary">{{ row.summary || '（无摘要）' }}</span>
          </el-tooltip>
          <div class="muted">{{ sourceLabel(row.summary_source) }} · {{ row.chars }} 字正文</div>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="90">
        <template #default="{ row }">
          <span class="muted">{{ formatSize(row.size_bytes) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="140">
        <template #default="{ row }">
          <span class="muted">{{ formatTime(row.updated_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" :icon="View" @click="openDetail(row)">
            详情
          </el-button>
          <el-button size="small" link type="primary" :icon="Edit" @click="openEdit(row)">
            编辑
          </el-button>
          <el-button size="small" link type="danger" :icon="Delete" @click="removeDoc(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <div class="muted kb-empty">
          暂无文档。上传 md / txt，目录可在上传时填写或之后修改路径。
        </div>
      </template>
    </el-table>

    <div class="kb-pager">
      <span class="muted">当前目录：{{ currentDir }}</span>
      <el-pagination
        :current-page="page"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="sizes, prev, pager, next, total"
        background
        @current-change="onPageChange"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- 上传：目录默认根目录，可改 -->
    <el-dialog v-model="uploadVisible" title="上传知识库文档" width="520px">
      <el-form label-width="90px" size="small">
        <el-form-item label="作用域">
          <el-radio-group v-model="uploadForm.scope">
            <el-radio-button value="agent">Agent 共享</el-radio-button>
            <el-radio-button value="user">个人文档</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="uploadForm.scope === 'user'" label="user_key">
          <el-select
            v-model="uploadForm.user_key"
            placeholder="选择或输入终端用户标识"
            style="width: 100%"
            filterable
            allow-create
            default-first-option
          >
            <el-option v-for="key in userKeys" :key="key" :label="key" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="目录">
          <el-input v-model="uploadForm.dir" placeholder="留空即根目录，例如 售后/退货" />
        </el-form-item>
        <el-form-item label="文件">
          <el-upload
            v-model:file-list="uploadFiles"
            drag
            :auto-upload="false"
            :limit="1"
            accept=".md,.markdown,.txt,.rst,.json,.yaml,.yml,.csv"
            style="width: 100%"
          >
            <div class="muted">拖入或点击选择 md / txt（≤2MB，UTF-8）</div>
          </el-upload>
        </el-form-item>
        <el-form-item label="摘要">
          <el-input
            v-model="uploadForm.summary"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="留空由工厂生成；填写后标记为人工摘要，同路径重传不会覆盖"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- 编辑：弹窗 -->
    <el-dialog v-model="editVisible" title="编辑文档" width="560px">
      <el-form label-width="80px" size="small">
        <el-form-item label="标题">
          <el-input v-model="editForm.title" />
        </el-form-item>
        <el-form-item label="路径">
          <el-input v-model="editForm.path" placeholder="售后/退货/退货政策.md" />
          <div class="muted">修改目录即移动文件；同名冲突会被拒绝。</div>
        </el-form-item>
        <el-form-item label="摘要">
          <el-input
            v-model="editForm.summary"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 6 }"
            placeholder="进入上下文的目录摘要"
          />
          <div class="muted">保存后标记为人工摘要，不会被重新上传覆盖。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 详情：抽屉 -->
    <el-drawer v-model="detailVisible" title="文档详情" size="480px">
      <template v-if="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="标题">{{ detail.title || fileName(detail.path) }}</el-descriptions-item>
          <el-descriptions-item label="路径">{{ detail.path }}</el-descriptions-item>
          <el-descriptions-item label="归属">
            <el-tag size="small" effect="plain">
              {{ detail.scope === 'user' ? `个人文档 · ${detail.user_key}` : 'Agent 共享' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="摘要">
            {{ detail.summary || '（无摘要）' }}
          </el-descriptions-item>
          <el-descriptions-item label="摘要来源">
            {{ sourceLabel(detail.summary_source) }}
          </el-descriptions-item>
          <el-descriptions-item label="大小">
            {{ formatSize(detail.size_bytes) }} · {{ detail.chars }} 字
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatTime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatTime(detail.updated_at) }}</el-descriptions-item>
          <el-descriptions-item label="sha256">
            <span class="muted kb-hash">{{ detail.sha256 || '-' }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <h4 class="kb-detail__title">正文预览</h4>
        <div v-loading="detailLoading" class="kb-preview">
          <pre v-if="detailBody">{{ detailBody }}</pre>
          <div v-else-if="!detailLoading" class="muted">正文为空。</div>
        </div>
        <div v-if="detailTotalChars" class="muted" style="margin-top: 6px">
          共 {{ detailTotalChars }} 字{{ detailTruncated ? '，仅预览前 8000 字' : '' }}
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.kb-manager {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
}
.kb-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.kb-toolbar__actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
.kb-doc {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.kb-doc__title {
  font-weight: 500;
}
.kb-doc__path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kb-summary {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kb-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.kb-empty {
  padding: 16px 0;
}
.kb-detail__title {
  margin: 16px 0 8px;
  font-size: 14px;
}
.kb-preview {
  max-height: 320px;
  overflow: auto;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  padding: 10px;
}
.kb-preview pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.kb-hash {
  word-break: break-all;
}
.muted {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}
</style>
