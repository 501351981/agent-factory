<script setup lang="ts">
import { nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Key, Plus, Upload } from '@element-plus/icons-vue'
import type { UploadRequestOptions } from 'element-plus'
import { api, resolveMediaUrl, type Agent, type ModelProfile } from '../api/client'
import CodeEditor from '../components/CodeEditor.vue'
import PageHeader from '../components/PageHeader.vue'
import { useClientPager } from '../composables/useClientPager'
import { CHAT_WEB_ORIGIN } from '../env'

type EditorSectionId =
  | 'basic'
  | 'welcome'
  | 'prompt'
  | 'tools'
  | 'skills'
  | 'memory'

type EditorSection = {
  id: EditorSectionId
  label: string
}

const SECTIONS: EditorSection[] = [
  { id: 'basic', label: '基本信息' },
  { id: 'welcome', label: '欢迎页' },
  { id: 'prompt', label: '系统提示词' },
  { id: 'tools', label: 'Tools' },
  { id: 'skills', label: 'Skills' },
  { id: 'memory', label: '记忆' },
]

const agents = ref<Agent[]>([])
const modelProfiles = ref<ModelProfile[]>([])
const { pager, pagedRows: pagedAgents, onPageSizeChange } = useClientPager(agents)
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<Agent | null>(null)
const embedVisible = ref(false)
const embedSnippet = ref('')
const embedAgentId = ref('')
const credVisible = ref(false)
const credPlainSecret = ref('')
const credAccessKey = ref('')
const credBusy = ref(false)
const logoUploading = ref(false)

const editorBodyRef = ref<HTMLElement | null>(null)
const activeSection = ref<EditorSectionId>('basic')

const defaultToolsJson = () =>
  JSON.stringify(
    [
      {
        name: 'echo',
        description: '回显文本',
        execution: 'client',
        parameters: {
          type: 'object',
          properties: { text: { type: 'string' } },
          required: ['text'],
        },
      },
      {
        name: 'get_cwd',
        description: '获取本地当前目录',
        execution: 'client',
        parameters: { type: 'object', properties: {} },
      },
      {
        name: 'list_dir',
        description: '列出本地目录（执行前需用户审批）',
        execution: 'client',
        require_approval: true,
        parameters: {
          type: 'object',
          properties: { path: { type: 'string' } },
        },
      },
    ],
    null,
    2,
  )

/** 仅目录：name + description。正文在客户端 skills/<name>/SKILL.md。 */
const defaultSkillsJson = () =>
  JSON.stringify(
    [
      {
        name: 'workspace-scout',
        description:
          '本地目录/工作区侦察。用户问当前目录、列文件、cwd、workspace 时先 skill_load 再执行。',
      },
      {
        name: 'stock-quote',
        description: '查询百度、阿里巴巴股价。用户询问这两只股票的价格或行情时使用。',
      },
    ],
    null,
    2,
  )

const defaultPrompts = () => ({
  zh: '你是演示助手。请始终使用中文思考与回复。当前用户 ID 为 {{userId}}。需要时可调用 echo / get_cwd / list_dir 工具。',
  en: 'You are a demo assistant. Always think and reply in English. Current user ID is {{userId}}. You may call echo / get_cwd / list_dir when needed.',
  ja: 'あなたはデモ助手です。思考と回答は常に日本語で行ってください。現在のユーザーIDは {{userId}} です。必要に応じて echo / get_cwd / list_dir を呼び出してください。',
})

const defaultSuggested = () => [
  '帮我梳理今天的工作重点',
  '用简单的话解释一个复杂概念',
  '帮我写一封礼貌的邮件草稿',
]

const defaultMemory = () => ({
  enabled: true,
  max_profile_items: 30,
  max_playbook_items: 80,
  max_profile_chars: 500,
  max_playbook_chars: 200,
  max_detail_chars: 6000,
  profile_top_k: 30,
  playbook_top_k: 40,
  max_chars: 8000,
  evict_when_full: true,
})

const form = reactive({
  name: 'demo-assistant',
  promptTab: 'zh' as 'zh' | 'en' | 'ja',
  model_profile_id: '' as string,
  logo_url: '',
  welcome_title: '有什么可以帮忙的？',
  welcome_subtitle: '',
  system_prompts: defaultPrompts(),
  toolsJson: defaultToolsJson(),
  skillsJson: defaultSkillsJson(),
  suggested_prompts: defaultSuggested() as string[],
  memory: defaultMemory(),
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

function resetForm() {
  form.name = 'demo-assistant'
  form.promptTab = 'zh'
  const defaultModel = modelProfiles.value.find((item) => item.is_default)
  form.model_profile_id = defaultModel?.id || modelProfiles.value[0]?.id || ''
  form.logo_url = ''
  form.welcome_title = '有什么可以帮忙的？'
  form.welcome_subtitle = ''
  form.system_prompts = defaultPrompts()
  form.toolsJson = defaultToolsJson()
  form.skillsJson = defaultSkillsJson()
  form.suggested_prompts = defaultSuggested()
  form.memory = defaultMemory()
  activeSection.value = 'basic'
}

function addSuggestedPrompt() {
  if (form.suggested_prompts.length >= 12) return
  form.suggested_prompts.push('')
}

function removeSuggestedPrompt(index: number) {
  form.suggested_prompts.splice(index, 1)
  if (!form.suggested_prompts.length) form.suggested_prompts.push('')
}

function clearLogo() {
  form.logo_url = ''
}

async function uploadLogoRequest(options: UploadRequestOptions) {
  const raw = options.file as File
  if (!raw.type.startsWith('image/')) {
    ElMessage.warning('请上传图片文件')
    return
  }
  if (raw.size > 2 * 1024 * 1024) {
    ElMessage.warning('图片不能超过 2MB')
    return
  }
  logoUploading.value = true
  try {
    const uploaded = await api.uploadLogo(raw)
    form.logo_url = uploaded.url
    ElMessage.success('Logo 已上传')
    options.onSuccess?.(uploaded)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    ElMessage.error(message)
    options.onError?.(err as Error)
  } finally {
    logoUploading.value = false
  }
}

function openCreate() {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(agent: Agent) {
  editing.value = agent
  form.name = agent.name
  form.promptTab = 'zh'
  form.model_profile_id = agent.model_profile_id || ''
  form.logo_url = agent.logo_url || ''
  form.welcome_title = agent.welcome_title || '有什么可以帮忙的？'
  form.welcome_subtitle = agent.welcome_subtitle || ''
  const prompts = agent.system_prompts || {}
  form.system_prompts = {
    zh: prompts.zh || agent.system_prompt || '',
    en: prompts.en || '',
    ja: prompts.ja || '',
  }
  form.toolsJson = JSON.stringify(agent.tools || [], null, 2)
  form.skillsJson = JSON.stringify(agent.skills || [], null, 2)
  form.suggested_prompts = (agent.suggested_prompts || []).length
    ? [...(agent.suggested_prompts || [])]
    : ['']
  form.memory = { ...defaultMemory(), ...(agent.runtime?.memory || {}) }
  activeSection.value = 'basic'
  dialogVisible.value = true
}

let scrollingToSection = false

async function scrollToSection(id: EditorSectionId) {
  activeSection.value = id
  scrollingToSection = true
  await nextTick()
  const root = editorBodyRef.value
  const el = root?.querySelector(`[data-section="${id}"]`) as HTMLElement | null
  if (!el || !root) {
    scrollingToSection = false
    return
  }
  root.scrollTo({
    top: Math.max(0, el.offsetTop - 8),
    behavior: 'smooth',
  })
  window.setTimeout(() => {
    scrollingToSection = false
  }, 450)
}

function onEditorScroll() {
  if (scrollingToSection) return
  const root = editorBodyRef.value
  if (!root) return
  const anchor = root.scrollTop + 28
  let current: EditorSectionId = SECTIONS[0].id
  for (const section of SECTIONS) {
    const el = root.querySelector(`[data-section="${section.id}"]`) as HTMLElement | null
    if (!el) continue
    if (el.offsetTop <= anchor) current = section.id
  }
  if (activeSection.value !== current) activeSection.value = current
}

function parseJsonField(raw: string, label: string): unknown {
  try {
    return JSON.parse(raw || 'null')
  } catch {
    throw new Error(`${label} JSON 无效，请检查格式`)
  }
}

function buildPromptsPayload() {
  const system_prompts = {
    zh: form.system_prompts.zh.trim(),
    en: form.system_prompts.en.trim(),
    ja: form.system_prompts.ja.trim(),
  }
  if (!system_prompts.zh && !system_prompts.en && !system_prompts.ja) {
    throw new Error('请至少填写一种语言的系统提示词')
  }
  return system_prompts
}

function buildToolsPayload() {
  const parsed = parseJsonField(form.toolsJson, 'Tools')
  if (!Array.isArray(parsed)) throw new Error('Tools 必须是 JSON 数组')
  return parsed
}

function buildSkillsPayload() {
  const parsed = parseJsonField(form.skillsJson, 'Skills')
  if (!Array.isArray(parsed)) throw new Error('Skills 必须是 JSON 数组')
  return parsed.map((item, index) => {
    const row = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>
    const name = String(row.name || '').trim()
    if (!name) throw new Error(`第 ${index + 1} 个 Skill 缺少 name`)
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
      throw new Error(`Skill「${name}」名称须为小写字母/数字/连字符`)
    }
    return {
      name,
      description: String(row.description || '').trim(),
    }
  })
}

function buildSuggestedPayload() {
  return form.suggested_prompts
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 12)
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

async function save() {
  saving.value = true
  try {
    if (!form.name.trim()) {
      ElMessage.warning('请填写智能体名称')
      await scrollToSection('basic')
      return
    }
    const system_prompts = buildPromptsPayload()
    const tools = buildToolsPayload()
    const skills = buildSkillsPayload()
    const suggested_prompts = buildSuggestedPayload()
    const body = {
      name: form.name.trim(),
      system_prompt: system_prompts.zh || system_prompts.en || system_prompts.ja,
      system_prompts,
      model_profile_id: form.model_profile_id || null,
      model: null,
      tools,
      skills,
      logo_url: form.logo_url.trim() || null,
      welcome_title: form.welcome_title.trim() || null,
      welcome_subtitle: form.welcome_subtitle.trim() || null,
      suggested_prompts,
      runtime: {
        ...(editing.value?.runtime || {}),
        memory: { ...form.memory },
      },
    }
    if (editing.value) {
      await api.updateAgent(editing.value.id, body)
      ElMessage.success('保存成功')
    } else {
      const created = await api.createAgent(body)
      ElMessage.success('创建成功')
      embedAgentId.value = created.id
      embedSnippet.value = buildEmbed(created.id)
      embedVisible.value = true
    }
    dialogVisible.value = false
    await refresh()
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    if (message.includes('系统提示词')) void scrollToSection('prompt')
    else if (message.includes('Tools')) void scrollToSection('tools')
    else if (message.includes('Skill')) void scrollToSection('skills')
    else if (message.includes('推荐问')) void scrollToSection('welcome')
    else if (message.includes('名称')) void scrollToSection('basic')
    ElMessage.error(message)
  } finally {
    saving.value = false
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
      description="配置提示词、模型、Tools / Skills，并生成业务嵌入代码"
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
      v-model="dialogVisible"
      :title="editing ? '编辑智能体' : '新建智能体'"
      width="1100px"
      top="3vh"
      class="agent-editor-dialog"
      destroy-on-close
      append-to-body
    >
      <div class="agent-editor">
        <aside class="agent-editor__nav" aria-label="配置分组导航">
          <button
            v-for="section in SECTIONS"
            :key="section.id"
            type="button"
            class="agent-editor__nav-item"
            :class="{ 'is-active': activeSection === section.id }"
            @click="scrollToSection(section.id)"
          >
            <span class="agent-editor__dot" aria-hidden="true" />
            <span class="agent-editor__nav-label">{{ section.label }}</span>
          </button>
        </aside>

        <div ref="editorBodyRef" class="agent-editor__body" @scroll.passive="onEditorScroll">
          <section data-section="basic" class="editor-section">
            <h3 class="editor-section__title">基本信息</h3>
            <el-form label-width="100px">
              <el-form-item label="名称" required>
                <el-input
                  v-model="form.name"
                  placeholder="请输入智能体名称"
                  maxlength="64"
                  show-word-limit
                />
              </el-form-item>
              <el-form-item label="绑定模型">
                <el-select
                  v-model="form.model_profile_id"
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
              <el-form-item label="Logo">
                <div class="logo-field">
                  <div class="logo-preview" :class="{ 'is-empty': !form.logo_url.trim() }">
                    <img
                      v-if="form.logo_url.trim()"
                      :src="resolveMediaUrl(form.logo_url)"
                      alt="logo preview"
                    />
                    <span v-else>暂无</span>
                  </div>
                  <div class="logo-actions">
                    <el-upload
                      :show-file-list="false"
                      accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
                      :http-request="uploadLogoRequest"
                      :disabled="logoUploading"
                    >
                      <el-button :icon="Upload" :loading="logoUploading">
                        {{ form.logo_url.trim() ? '重新上传' : '上传图片' }}
                      </el-button>
                    </el-upload>
                    <el-button
                      v-if="form.logo_url.trim()"
                      :icon="Delete"
                      @click="clearLogo"
                    >
                      清除
                    </el-button>
                    <div class="hint">支持 png / jpg / webp / gif / svg，最大 2MB</div>
                    <el-input
                      v-model="form.logo_url"
                      placeholder="也可粘贴外链 URL 或 /uploads/..."
                      clearable
                    />
                  </div>
                </div>
              </el-form-item>
            </el-form>
          </section>

          <section data-section="welcome" class="editor-section">
            <h3 class="editor-section__title">欢迎页</h3>
            <el-form label-width="100px">
              <el-form-item label="欢迎语">
                <el-input
                  v-model="form.welcome_title"
                  placeholder="有什么可以帮忙的？"
                  maxlength="80"
                  show-word-limit
                />
              </el-form-item>
              <el-form-item label="欢迎副标题">
                <el-input
                  v-model="form.welcome_subtitle"
                  type="textarea"
                  :rows="2"
                  placeholder="一句话介绍能力，或使用说明"
                  maxlength="200"
                  show-word-limit
                />
              </el-form-item>
              <el-form-item label="推荐问">
                <div class="suggested-list">
                  <div
                    v-for="(_, index) in form.suggested_prompts"
                    :key="index"
                    class="suggested-row"
                  >
                    <el-input
                      v-model="form.suggested_prompts[index]"
                      placeholder="一条推荐问题，点击后填入聊天输入框"
                      maxlength="120"
                      show-word-limit
                    />
                    <el-button
                      :icon="Delete"
                      circle
                      @click="removeSuggestedPrompt(index)"
                    />
                  </div>
                  <el-button
                    type="primary"
                    link
                    :icon="Plus"
                    :disabled="form.suggested_prompts.length >= 12"
                    @click="addSuggestedPrompt"
                  >
                    添加推荐问
                  </el-button>
                  <div class="hint">最多 12 条；新建会话空白页展示。</div>
                </div>
              </el-form-item>
            </el-form>
          </section>

          <section data-section="prompt" class="editor-section">
            <h3 class="editor-section__title">系统提示词</h3>
            <div class="hint" style="margin-bottom: 10px">
              按语言编写 Markdown 提示词；可含
              <code v-pre>{{userId}}</code> 等变量。Session 传
              <code>locale</code> 选用对应模板，缺省回退 zh→en→ja。
            </div>
            <el-radio-group v-model="form.promptTab" style="margin-bottom: 10px">
              <el-radio-button value="zh">中文</el-radio-button>
              <el-radio-button value="en">English</el-radio-button>
              <el-radio-button value="ja">日本語</el-radio-button>
            </el-radio-group>
            <CodeEditor
              v-model="form.system_prompts[form.promptTab]"
              mode="markdown"
              :rows="16"
              :placeholder="`请用该语言撰写系统提示词（支持 Markdown）`"
            />
          </section>

          <section data-section="tools" class="editor-section">
            <h3 class="editor-section__title">Tools</h3>
            <div class="hint" style="margin-bottom: 8px">
              JSON 数组。字段：<code>name</code> / <code>description</code> /
              <code>execution</code>（client | remote_http | builtin）/
              <code>endpoint</code>（remote_http）/ <code>parameters</code> /
              <code>require_approval</code>（执行前审批）
            </div>
            <CodeEditor v-model="form.toolsJson" mode="json" :rows="18" placeholder="[]" />
          </section>

          <section data-section="skills" class="editor-section">
            <h3 class="editor-section__title">Skills</h3>
            <div class="hint" style="margin-bottom: 8px">
              JSON 数组，仅存 <code>name</code> + <code>description</code>；正文在客户端
              <code>skills/&lt;name&gt;/SKILL.md</code>
            </div>
            <CodeEditor v-model="form.skillsJson" mode="json" :rows="14" placeholder="[]" />
          </section>

          <section data-section="memory" class="editor-section">
            <h3 class="editor-section__title">记忆</h3>
            <el-form label-width="120px">
              <el-form-item label="启用记忆">
                <el-switch v-model="form.memory.enabled" />
              </el-form-item>
              <template v-if="form.memory.enabled">
                <el-form-item label="满额淘汰">
                  <el-switch v-model="form.memory.evict_when_full" />
                  <span class="hint inline">关闭后超配额直接写失败</span>
                </el-form-item>
                <el-form-item label="画像条数">
                  <el-input-number v-model="form.memory.max_profile_items" :min="1" :max="200" />
                </el-form-item>
                <el-form-item label="实践目录">
                  <el-input-number v-model="form.memory.max_playbook_items" :min="1" :max="500" />
                </el-form-item>
                <el-form-item label="画像单条字数">
                  <el-input-number v-model="form.memory.max_profile_chars" :min="50" :max="5000" />
                </el-form-item>
                <el-form-item label="目录单条字数">
                  <el-input-number v-model="form.memory.max_playbook_chars" :min="20" :max="2000" />
                </el-form-item>
                <el-form-item label="明细单条字数">
                  <el-input-number v-model="form.memory.max_detail_chars" :min="200" :max="50000" />
                </el-form-item>
                <el-form-item label="注入画像 TopK">
                  <el-input-number v-model="form.memory.profile_top_k" :min="1" :max="100" />
                </el-form-item>
                <el-form-item label="注入目录 TopK">
                  <el-input-number v-model="form.memory.playbook_top_k" :min="1" :max="200" />
                </el-form-item>
                <el-form-item label="注入总字数帽">
                  <el-input-number v-model="form.memory.max_chars" :min="500" :max="50000" />
                </el-form-item>
              </template>
            </el-form>
          </section>
        </div>
      </div>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="embedVisible" title="业务嵌入说明" width="720px">
      <el-alert
        title="浏览器只能持有短时 embed_token，Secret Key 留在业务后端。"
        type="success"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <pre class="embed-code">{{ embedSnippet }}</pre>
      <template #footer>
        <el-button type="primary" @click="recopyEmbed">再次复制</el-button>
        <el-button @click="embedVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="credVisible" title="凭证已生成（请立即保存）" width="640px">
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
        <el-button
          type="primary"
          @click="navigator.clipboard.writeText(`${credAccessKey}\n${credPlainSecret}`)"
        >
          复制 AK/SK
        </el-button>
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
}
.hint.inline {
  margin-left: 8px;
}
.suggested-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.suggested-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.logo-field {
  width: 100%;
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.logo-preview {
  width: 64px;
  height: 64px;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid var(--af-border);
  background: #fff;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #94a3b8;
  font-size: 12px;
}
.logo-preview.is-empty {
  background: #f8fafc;
}
.logo-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.logo-actions {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.logo-actions .el-input {
  width: 100%;
  margin-top: 4px;
}

.agent-editor {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 12px;
  height: min(74vh, calc(100vh - 160px));
  min-height: 420px;
  padding: 12px;
  border-radius: 14px;
  overflow: hidden;
  background: #eef2f7;
}

.agent-editor__nav {
  display: flex;
  flex-direction: column;
  padding: 18px 8px 18px 14px;
  min-height: 0;
  overflow: auto;
  background: transparent;
}

.agent-editor__nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 36px;
  padding: 6px 0 6px 0;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
  color: #94a3b8;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.3;
  white-space: nowrap;
}

.agent-editor__nav-item::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 22px;
  bottom: -6px;
  width: 1px;
  background: #cbd5e1;
}

.agent-editor__nav-item:last-child::before {
  display: none;
}

.agent-editor__dot {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 2px solid #cbd5e1;
  background: #eef2f7;
  flex-shrink: 0;
  position: relative;
  z-index: 1;
  box-sizing: border-box;
  transition:
    border-color 0.15s ease,
    background 0.15s ease,
    box-shadow 0.15s ease;
}

.agent-editor__nav-label {
  transition: color 0.15s ease;
}

.agent-editor__nav-item:hover {
  color: #475569;
}

.agent-editor__nav-item:hover .agent-editor__dot {
  border-color: #94a3b8;
}

.agent-editor__nav-item.is-active {
  color: #0f172a;
  font-weight: 600;
}

.agent-editor__nav-item.is-active .agent-editor__dot {
  border-color: #0f172a;
  background: #0f172a;
  box-shadow: 0 0 0 3px rgba(15, 23, 42, 0.08);
}

.agent-editor__body {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 0 2px 4px 0;
  scroll-behavior: smooth;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.editor-section {
  margin: 0;
  padding: 18px 20px 16px;
  background: #fff;
  border-radius: 12px;
}

.editor-section__title {
  margin: 0 0 14px;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.01em;
  color: #0f172a;
}
</style>

<style>
.agent-editor-dialog.el-dialog {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 6vh);
  margin-bottom: 0;
}

.agent-editor-dialog .el-dialog__body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  padding-top: 8px;
  padding-bottom: 8px;
}

.agent-editor-dialog .el-dialog__footer {
  flex-shrink: 0;
}
</style>
