<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Check, Delete, Key, Plus, Refresh, Upload } from '@element-plus/icons-vue'
import type { UploadRequestOptions } from 'element-plus'
import {
  api,
  resolveMediaUrl,
  type Agent,
  type AgentSkillMount,
  type ModelProfile,
  type PlatformSkillPackage,
  type SkillPackage,
} from '../api/client'
import CodeEditor from '../components/CodeEditor.vue'
import PageHeader from '../components/PageHeader.vue'
import { CHAT_WEB_ORIGIN } from '../env'

type EditorSectionId =
  | 'basic'
  | 'welcome'
  | 'prompt'
  | 'tools'
  | 'skills'
  | 'memory'
  | 'limits'
  | 'theme'

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
  { id: 'limits', label: '运行限制' },
  { id: 'theme', label: '主题样式' },
]

/** Color / radius tokens aligned with chat-ui ``--af-*``. */
type ThemeField = {
  key: string
  label: string
  kind: 'color' | 'length'
  group: 'page' | 'user' | 'assistant' | 'composer' | 'radius'
}

const THEME_GROUPS: { id: ThemeField['group']; label: string }[] = [
  { id: 'page', label: '页面背景' },
  { id: 'user', label: '用户消息' },
  { id: 'assistant', label: '助手消息' },
  { id: 'composer', label: '聊天输入框' },
  { id: 'radius', label: '圆角' },
]

const THEME_FIELDS: ThemeField[] = [
  { key: 'bg', label: '页背景', kind: 'color', group: 'page' },
  { key: 'surface', label: '表面/卡片', kind: 'color', group: 'page' },
  { key: 'border', label: '边框', kind: 'color', group: 'page' },
  { key: 'borderSoft', label: '弱边框', kind: 'color', group: 'page' },
  { key: 'text', label: '正文色', kind: 'color', group: 'page' },
  { key: 'muted', label: '次要文字', kind: 'color', group: 'page' },
  { key: 'userBubble', label: '气泡背景', kind: 'color', group: 'user' },
  { key: 'userBubbleText', label: '气泡文字', kind: 'color', group: 'user' },
  { key: 'userBubbleBorder', label: '气泡边框', kind: 'color', group: 'user' },
  { key: 'assistantBubble', label: '气泡背景', kind: 'color', group: 'assistant' },
  { key: 'assistantBubbleText', label: '气泡文字', kind: 'color', group: 'assistant' },
  { key: 'assistantBubbleBorder', label: '气泡边框', kind: 'color', group: 'assistant' },
  { key: 'composerBg', label: '输入区背景', kind: 'color', group: 'composer' },
  { key: 'composerCard', label: '输入卡片', kind: 'color', group: 'composer' },
  { key: 'composerBorder', label: '卡片边框', kind: 'color', group: 'composer' },
  { key: 'composerText', label: '输入文字', kind: 'color', group: 'composer' },
  { key: 'composerMuted', label: '占位/次要', kind: 'color', group: 'composer' },
  { key: 'composerSendBg', label: '发送按钮底', kind: 'color', group: 'composer' },
  { key: 'composerSendText', label: '发送按钮字', kind: 'color', group: 'composer' },
  { key: 'radiusLg', label: '大圆角', kind: 'length', group: 'radius' },
  { key: 'radiusMd', label: '中圆角', kind: 'length', group: 'radius' },
  { key: 'radiusBubble', label: '气泡圆角', kind: 'length', group: 'radius' },
]

function themeFieldsOf(group: ThemeField['group']) {
  return THEME_FIELDS.filter((f) => f.group === group)
}

const defaultUiTheme = (): Record<string, string> => ({})

type ToolRow = {
  name: string
  description: string
  execution: 'client' | 'remote_http' | 'builtin'
  endpoint: string
  require_approval: boolean
  parametersJson: string
  approvalPolicyJson: string
}

type ListEditMode = 'table' | 'json'

function emptyToolRow(): ToolRow {
  return {
    name: '',
    description: '',
    execution: 'client',
    endpoint: '',
    require_approval: false,
    parametersJson: '{\n  "type": "object",\n  "properties": {}\n}',
    approvalPolicyJson: '',
  }
}

function toolsToRows(raw: unknown): ToolRow[] {
  if (!Array.isArray(raw)) return []
  return raw.map((item) => {
    const row = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>
    const executionRaw = String(row.execution || 'client')
    const execution =
      executionRaw === 'remote_http' || executionRaw === 'builtin' ? executionRaw : 'client'
    let parametersJson = '{\n  "type": "object",\n  "properties": {}\n}'
    try {
      parametersJson = JSON.stringify(
        row.parameters && typeof row.parameters === 'object'
          ? row.parameters
          : { type: 'object', properties: {} },
        null,
        2,
      )
    } catch {
      /* keep default */
    }
    let approvalPolicyJson = ''
    if (row.approval_policy && typeof row.approval_policy === 'object') {
      try {
        approvalPolicyJson = JSON.stringify(row.approval_policy, null, 2)
      } catch {
        approvalPolicyJson = ''
      }
    }
    return {
      name: String(row.name || ''),
      description: String(row.description || ''),
      execution,
      endpoint: String(row.endpoint || ''),
      require_approval: Boolean(row.require_approval),
      parametersJson,
      approvalPolicyJson,
    }
  })
}

function rowsToTools(rows: ToolRow[]): unknown[] {
  return rows
    .map((row) => {
      const name = row.name.trim()
      if (!name) return null
      let parameters: unknown = { type: 'object', properties: {} }
      try {
        parameters = JSON.parse(row.parametersJson || '{}')
      } catch {
        throw new Error(`Tool「${name}」的 parameters JSON 无效`)
      }
      let approval_policy: unknown = null
      const policyRaw = (row.approvalPolicyJson || '').trim()
      if (policyRaw) {
        try {
          approval_policy = JSON.parse(policyRaw)
        } catch {
          throw new Error(`Tool「${name}」的 approval_policy JSON 无效`)
        }
      }
      const execution = row.execution
      const endpoint = row.endpoint.trim()
      if (execution === 'remote_http' && !endpoint) {
        throw new Error(`Tool「${name}」为 remote_http 时必须填写 endpoint`)
      }
      return {
        name,
        description: row.description.trim(),
        execution,
        endpoint: endpoint || null,
        parameters,
        require_approval: Boolean(row.require_approval),
        approval_policy,
      }
    })
    .filter(Boolean)
}

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const logoUploading = ref(false)
const packageUploading = ref(false)
const packagesLoading = ref(false)
const agent = ref<Agent | null>(null)
const modelProfiles = ref<ModelProfile[]>([])
const skillPackages = ref<SkillPackage[]>([])
const skillMounts = ref<AgentSkillMount[]>([])
const platformLibrary = ref<PlatformSkillPackage[]>([])
const mountBusy = ref<string | null>(null)
const toolsEditMode = ref<ListEditMode>('table')
const activeSection = ref<EditorSectionId>('basic')
const editorBodyRef = ref<HTMLElement | null>(null)

const skillTotalCount = computed(
  () => skillMounts.value.length + skillPackages.value.length,
)

const unmountedPlatformSkills = computed(() => {
  const mounted = new Set(skillMounts.value.map((m) => m.name))
  return platformLibrary.value.filter((p) => p.status === 'active' && !mounted.has(p.name))
})

const embedVisible = ref(false)
const embedSnippet = ref('')
const embedAgentId = ref('')
const credVisible = ref(false)
const credBusy = ref(false)
const credAccessKey = ref('')
const credPlainSecret = ref('')

const agentId = computed(() => String(route.params.id || ''))
const pageTitle = computed(() => agent.value?.name || '编辑智能体')

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
  toolsRows: toolsToRows(JSON.parse(defaultToolsJson())) as ToolRow[],
  suggested_prompts: defaultSuggested() as string[],
  ui_theme: defaultUiTheme() as Record<string, string>,
  memory: defaultMemory(),
  runtime_max_rounds: 8,
  runtime_on_max_rounds: 'summarize' as 'summarize' | 'stop',
  runtime_max_run_tokens: 0,
  runtime_parallel: true,
  runtime_tool_timeout: 30,
  runtime_run_timeout: 300,
})

async function loadModels() {
  try {
    const modelList = await api.listModels()
    modelProfiles.value = modelList.filter((item) => item.enabled)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function fillForm(row: Agent) {
  form.name = row.name
  form.promptTab = 'zh'
  form.model_profile_id = row.model_profile_id || ''
  form.logo_url = row.logo_url || ''
  form.welcome_title = row.welcome_title || '有什么可以帮忙的？'
  form.welcome_subtitle = row.welcome_subtitle || ''
  const prompts = row.system_prompts || {}
  form.system_prompts = {
    zh: prompts.zh || row.system_prompt || '',
    en: prompts.en || '',
    ja: prompts.ja || '',
  }
  form.toolsJson = JSON.stringify(row.tools || [], null, 2)
  form.toolsRows = toolsToRows(row.tools || [])
  form.suggested_prompts = (row.suggested_prompts || []).length
    ? [...(row.suggested_prompts || [])]
    : ['']
  form.ui_theme = row.ui_theme && typeof row.ui_theme === 'object' ? { ...row.ui_theme } : {}
  form.memory = { ...defaultMemory(), ...(row.runtime?.memory || {}) }
  const runtime = row.runtime || {}
  form.runtime_max_rounds = Number(runtime.max_tool_rounds ?? 8)
  form.runtime_on_max_rounds = runtime.on_max_rounds === 'stop' ? 'stop' : 'summarize'
  form.runtime_max_run_tokens = Number(runtime.max_run_tokens ?? 0)
  form.runtime_parallel = runtime.parallel_tool_calls !== false
  form.runtime_tool_timeout = Number(runtime.tool_timeout_seconds ?? 30)
  form.runtime_run_timeout = Number(runtime.run_timeout_seconds ?? 300)
  toolsEditMode.value = 'table'
  activeSection.value = 'basic'
}

async function loadPackages() {
  if (!agentId.value) return
  packagesLoading.value = true
  try {
    const [owned, mounts, platform] = await Promise.all([
      api.listSkillPackages(agentId.value),
      api.listSkillMounts(agentId.value),
      api.listPlatformSkills(),
    ])
    skillPackages.value = owned
    skillMounts.value = mounts
    platformLibrary.value = platform
    syncDescDrafts(skillPackages.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    packagesLoading.value = false
  }
}

async function mountPlatform(name: string) {
  if (!agent.value) return
  mountBusy.value = name
  try {
    await api.mountPlatformSkill(agent.value.id, name)
    ElMessage.success(`已启用 ${name}`)
    await loadPackages()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    mountBusy.value = null
  }
}

async function unmountPlatform(row: AgentSkillMount) {
  if (!agent.value) return
  try {
    await ElMessageBox.confirm(
      `取消启用平台技能「${row.name}」？不会删除平台库中的正文。`,
      '取消启用',
      { type: 'warning', confirmButtonText: '取消启用', cancelButtonText: '返回' },
    )
    await api.unmountPlatformSkill(agent.value.id, row.name)
    ElMessage.success('已取消启用')
    await loadPackages()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function loadAgent() {
  if (!agentId.value) {
    ElMessage.error('缺少智能体 ID')
    router.replace('/')
    return
  }
  loading.value = true
  try {
    const row = await api.getAgent(agentId.value)
    agent.value = row
    fillForm(row)
    await loadPackages()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    router.replace('/')
  } finally {
    loading.value = false
  }
}

const descDrafts = reactive<Record<string, string>>({})
const descSaving = ref<string | null>(null)
const updatingPackageName = ref<string | null>(null)

function syncDescDrafts(packages: SkillPackage[]) {
  for (const key of Object.keys(descDrafts)) {
    if (!packages.some((p) => p.name === key)) delete descDrafts[key]
  }
  for (const pkg of packages) {
    if (!(pkg.name in descDrafts)) descDrafts[pkg.name] = pkg.description || ''
  }
}

function packageScriptCount(pkg: SkillPackage) {
  return pkg.files.filter((f) => f.kind === 'script').length
}

function formatPackageUpdatedAt(value: string) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

function descriptionDirty(pkg: SkillPackage) {
  return (descDrafts[pkg.name] ?? '') !== (pkg.description || '')
}

async function savePackageDescription(pkg: SkillPackage) {
  if (!agent.value) return
  const next = (descDrafts[pkg.name] ?? '').trimEnd()
  descSaving.value = pkg.name
  try {
    const updated = await api.updateSkillPackage(agent.value.id, pkg.name, {
      description: next,
    })
    const idx = skillPackages.value.findIndex((p) => p.name === pkg.name)
    if (idx >= 0) skillPackages.value[idx] = updated
    descDrafts[pkg.name] = updated.description || ''
    ElMessage.success('描述已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    descSaving.value = null
  }
}

async function uploadPackageRequest(options: UploadRequestOptions) {
  if (!agent.value) return
  const raw = options.file as File
  if (!raw.name.toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请上传 .zip 技能包')
    return
  }
  packageUploading.value = true
  try {
    const installed = await api.uploadSkillPackage(agent.value.id, raw)
    ElMessage.success(`已安装 ${installed.name}`)
    if (installed.warnings?.length) {
      ElMessage.warning(installed.warnings.join('；'))
    }
    options.onSuccess?.(installed)
    await loadAgent()
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    if (message.includes('already installed') || message.includes('skill_exists')) {
      try {
        await ElMessageBox.confirm(
          `同名技能已存在。覆盖后正文与 version 更新为新包内容（仅保留当前一份）。是否继续？`,
          '覆盖更新',
          { type: 'warning', confirmButtonText: '覆盖', cancelButtonText: '取消' },
        )
        let keepDescription = false
        try {
          await ElMessageBox.confirm(
            '是否保留管理台当前的 description（不采用 zip frontmatter）？',
            '保留描述？',
            {
              type: 'info',
              confirmButtonText: '保留现有描述',
              cancelButtonText: '用包内描述',
              distinguishCancelAndClose: true,
            },
          )
          keepDescription = true
        } catch (choice) {
          if (choice === 'close') return
          keepDescription = false
        }
        const installed = await api.uploadSkillPackage(agent.value.id, raw, {
          force: true,
          keepDescription,
        })
        ElMessage.success(`已覆盖更新 ${installed.name}`)
        options.onSuccess?.(installed)
        await loadAgent()
        return
      } catch {
        return
      }
    }
    ElMessage.error(message)
    options.onError?.(err as never)
  } finally {
    packageUploading.value = false
  }
}

async function updatePackageRequest(pkg: SkillPackage, options: UploadRequestOptions) {
  if (!agent.value) return
  const raw = options.file as File
  if (!raw.name.toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请上传 .zip 技能包')
    return
  }
  let keepDescription = false
  try {
    await ElMessageBox.confirm(
      `用新 zip 覆盖「${pkg.name}」。zip 内 name 须一致；仅保留当前一份，无历史版本。`,
      '更新技能包',
      { type: 'warning', confirmButtonText: '继续', cancelButtonText: '取消' },
    )
    try {
      await ElMessageBox.confirm(
        '是否保留管理台当前的 description？',
        '保留描述？',
        {
          type: 'info',
          confirmButtonText: '保留现有描述',
          cancelButtonText: '用包内描述',
          distinguishCancelAndClose: true,
        },
      )
      keepDescription = true
    } catch (choice) {
      if (choice === 'close') return
      keepDescription = false
    }
  } catch {
    return
  }

  updatingPackageName.value = pkg.name
  try {
    const installed = await api.uploadSkillPackage(agent.value.id, raw, {
      force: true,
      keepDescription,
      expectedName: pkg.name,
    })
    ElMessage.success(`已更新 ${installed.name}`)
    if (installed.warnings?.length) {
      ElMessage.warning(installed.warnings.join('；'))
    }
    options.onSuccess?.(installed)
    await loadAgent()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
    options.onError?.(err as never)
  } finally {
    updatingPackageName.value = null
  }
}

async function removePackage(pkg: SkillPackage) {
  if (!agent.value) return
  try {
    await ElMessageBox.confirm(
      `确认删除技能「${pkg.name}」？正文与目录条目会一并移除。`,
      '删除技能',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await api.deleteSkillPackage(agent.value.id, pkg.name)
    ElMessage.success('已删除')
    await loadAgent()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function goBack() {
  router.push('/')
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
  if (toolsEditMode.value === 'table') {
    const tools = rowsToTools(form.toolsRows)
    form.toolsJson = JSON.stringify(tools, null, 2)
    return tools
  }
  const parsed = parseJsonField(form.toolsJson, 'Tools')
  if (!Array.isArray(parsed)) throw new Error('Tools 必须是 JSON 数组')
  form.toolsRows = toolsToRows(parsed)
  return parsed
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
    await loadAgent()
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
    await loadAgent()
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
  if (!agent.value) return
  saving.value = true
  try {
    if (!form.name.trim()) {
      ElMessage.warning('请填写智能体名称')
      await scrollToSection('basic')
      return
    }
    const system_prompts = buildPromptsPayload()
    const tools = buildToolsPayload()
    const suggested_prompts = buildSuggestedPayload()
    const body = {
      name: form.name.trim(),
      system_prompt: system_prompts.zh || system_prompts.en || system_prompts.ja,
      system_prompts,
      model_profile_id: form.model_profile_id || null,
      model: null,
      tools,
      // skills catalog is owned by skill-package upload/delete — omit to preserve
      logo_url: form.logo_url.trim() || null,
      welcome_title: form.welcome_title.trim() || null,
      welcome_subtitle: form.welcome_subtitle.trim() || null,
      suggested_prompts,
      ui_theme: { ...form.ui_theme },
      runtime: {
        ...(agent.value.runtime || {}),
        max_tool_rounds: form.runtime_max_rounds,
        on_max_rounds: form.runtime_on_max_rounds,
        max_run_tokens: form.runtime_max_run_tokens,
        parallel_tool_calls: form.runtime_parallel,
        tool_timeout_seconds: form.runtime_tool_timeout,
        run_timeout_seconds: form.runtime_run_timeout,
        memory: { ...form.memory },
      },
    }
    const updated = await api.updateAgent(agent.value.id, body)
    agent.value = updated
    fillForm(updated)
    ElMessage.success('保存成功')
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    if (message.includes('系统提示词')) void scrollToSection('prompt')
    else if (message.includes('Tools')) void scrollToSection('tools')
    else if (message.includes('推荐问')) void scrollToSection('welcome')
    else if (message.includes('名称')) void scrollToSection('basic')
    else if (message.includes('max_tool_rounds') || message.includes('max_run_tokens'))
      void scrollToSection('limits')
    ElMessage.error(message)
  } finally {
    saving.value = false
  }
}

async function rotateCredentialsHere() {
  if (!agent.value) return
  await rotateCredentials(agent.value)
}

async function revokeCredentialsHere() {
  if (!agent.value) return
  await revokeCredentials(agent.value)
}

async function copyEmbedHere() {
  if (!agent.value) return
  await copyEmbed(agent.value.id)
}

function syncToolsRowsFromJson() {
  const parsed = parseJsonField(form.toolsJson, 'Tools')
  if (!Array.isArray(parsed)) throw new Error('Tools 必须是 JSON 数组')
  form.toolsRows = toolsToRows(parsed)
}

function syncToolsJsonFromRows() {
  form.toolsJson = JSON.stringify(rowsToTools(form.toolsRows), null, 2)
}

function setToolsEditMode(mode: ListEditMode) {
  try {
    if (mode === 'json' && toolsEditMode.value === 'table') syncToolsJsonFromRows()
    if (mode === 'table' && toolsEditMode.value === 'json') syncToolsRowsFromJson()
    toolsEditMode.value = mode
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

function addToolRow() {
  form.toolsRows.push(emptyToolRow())
}

function removeToolRow(index: number) {
  form.toolsRows.splice(index, 1)
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
    options.onError?.(err as never)
  } finally {
    logoUploading.value = false
  }
}

function applyThemePreset(preset: 'light' | 'dark') {
  if (preset === 'light') {
    form.ui_theme = { preset: 'light' }
    return
  }
  form.ui_theme = {
    preset: 'dark',
    bg: '#0f1115',
    surface: '#1a1d24',
    border: '#2a2f3a',
    borderSoft: 'rgba(255, 255, 255, 0.08)',
    text: '#f3f4f6',
    muted: '#9ca3af',
    accent: '#e5e7eb',
    accentSoft: '#252a33',
    userBubble: '#2563eb',
    userBubbleText: '#ffffff',
    userBubbleBorder: 'transparent',
    assistantBubble: '#1a1d24',
    assistantBubbleText: '#f3f4f6',
    assistantBubbleBorder: '#2a2f3a',
    composerBg: '#0f1115',
    composerCard: '#1a1d24',
    composerBorder: '#2a2f3a',
    composerText: '#f3f4f6',
    composerMuted: '#9ca3af',
    composerSendBg: '#3b82f6',
    composerSendText: '#ffffff',
    think: '#818cf8',
    thinkSoft: '#1e1b4b',
    tool: '#2dd4bf',
    toolSoft: '#134e4a',
    ok: '#4ade80',
    warn: '#fbbf24',
    danger: '#f87171',
  }
}

function clearTheme() {
  form.ui_theme = defaultUiTheme()
}

function themeColorModel(key: string) {
  return {
    get value() {
      return form.ui_theme[key] || ''
    },
    set value(next: string) {
      const text = String(next || '').trim()
      if (!text) {
        const { [key]: _, ...rest } = form.ui_theme
        form.ui_theme = rest
        return
      }
      form.ui_theme = { ...form.ui_theme, [key]: text }
    },
  }
}

async function copyCreds() {
  try {
    await navigator.clipboard.writeText(`${credAccessKey.value}\n${credPlainSecret.value}`)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择')
  }
}

watch(agentId, () => {
  void loadAgent()
})

onMounted(async () => {
  await loadModels()
  await loadAgent()
})
</script>

<template>
  <div v-loading="loading" class="agent-edit-page">
    <PageHeader :title="pageTitle" description="配置提示词、模型、Tools / Skills 与主题">
      <template #actions>
        <el-button :icon="ArrowLeft" @click="goBack">返回列表</el-button>
        <el-button v-if="agent" plain @click="copyEmbedHere">嵌入代码</el-button>
        <el-dropdown v-if="agent" trigger="click">
          <el-button plain :icon="Key" :loading="credBusy">凭证</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item :disabled="credBusy" @click="rotateCredentialsHere">
                {{ agent?.has_secret_key ? '轮换 SK' : '生成 AK/SK' }}
              </el-dropdown-item>
              <el-dropdown-item
                v-if="agent?.has_secret_key"
                divided
                @click="revokeCredentialsHere"
              >
                作废凭证
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button type="primary" :loading="saving" :disabled="!agent" @click="save">
          保存
        </el-button>
      </template>
    </PageHeader>

    <el-card v-if="agent" class="page-card editor-card" shadow="never">
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
            <div class="editor-section__head">
              <h3 class="editor-section__title">
                Tools
                <span class="editor-section__count">{{ form.toolsRows.length }}</span>
              </h3>
              <el-radio-group
                :model-value="toolsEditMode"
                size="small"
                @update:model-value="(v: string) => setToolsEditMode(v as ListEditMode)"
              >
                <el-radio-button value="table">表格</el-radio-button>
                <el-radio-button value="json">JSON</el-radio-button>
              </el-radio-group>
            </div>
            <div class="hint" style="margin-bottom: 8px">
              表格便于快速浏览；复杂
              <code>parameters</code>
              可在行内编辑 JSON，或切到 JSON 模式整表编辑。
            </div>
            <template v-if="toolsEditMode === 'table'">
              <el-table :data="form.toolsRows" border size="small" class="config-table">
                <el-table-column label="name" min-width="120">
                  <template #default="{ row }">
                    <el-input v-model="row.name" placeholder="tool_name" />
                  </template>
                </el-table-column>
                <el-table-column label="description" min-width="160">
                  <template #default="{ row }">
                    <el-input v-model="row.description" placeholder="给模型看的说明" />
                  </template>
                </el-table-column>
                <el-table-column label="execution" width="140">
                  <template #default="{ row }">
                    <el-select v-model="row.execution" style="width: 100%">
                      <el-option value="client" label="client" />
                      <el-option value="remote_http" label="remote_http" />
                      <el-option value="builtin" label="builtin" />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="endpoint" min-width="140">
                  <template #default="{ row }">
                    <el-input
                      v-model="row.endpoint"
                      :disabled="row.execution !== 'remote_http'"
                      placeholder="remote_http 必填"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="审批" width="72" align="center">
                  <template #default="{ row }">
                    <el-switch v-model="row.require_approval" />
                  </template>
                </el-table-column>
                <el-table-column label="parameters" min-width="160">
                  <template #default="{ row }">
                    <el-input
                      v-model="row.parametersJson"
                      type="textarea"
                      :rows="2"
                      placeholder='{"type":"object","properties":{}}'
                    />
                  </template>
                </el-table-column>
                <el-table-column label="approval_policy" min-width="180">
                  <template #default="{ row }">
                    <el-input
                      v-model="row.approvalPolicyJson"
                      type="textarea"
                      :rows="2"
                      :disabled="!row.require_approval"
                      placeholder='开启审批后可选：{"default":"require","allow":[...]}'
                    />
                  </template>
                </el-table-column>
                <el-table-column label="" width="56" align="center" fixed="right">
                  <template #default="{ $index }">
                    <el-button :icon="Delete" circle size="small" @click="removeToolRow($index)" />
                  </template>
                </el-table-column>
              </el-table>
              <el-button
                type="primary"
                link
                :icon="Plus"
                style="margin-top: 8px"
                @click="addToolRow"
              >
                添加 Tool
              </el-button>
            </template>
            <CodeEditor
              v-else
              v-model="form.toolsJson"
              mode="json"
              :rows="18"
              placeholder="[]"
            />
          </section>

          <section data-section="skills" class="editor-section">
            <div class="editor-section__head">
              <h3 class="editor-section__title">
                Skills
                <span class="editor-section__count">{{ skillTotalCount }}</span>
              </h3>
              <el-upload
                :show-file-list="false"
                accept=".zip,application/zip"
                :http-request="uploadPackageRequest"
                :disabled="packageUploading"
              >
                <el-button type="primary" :icon="Upload" :loading="packageUploading">
                  上传本 Agent 包
                </el-button>
              </el-upload>
            </div>
            <div class="hint" style="margin-bottom: 10px">
              平台技能在「平台技能」页维护，此处启用；本 Agent 包可改 description / 更新 zip。
              同名不可同时存在平台挂载与自有包。
            </div>
            <div v-loading="packagesLoading">
              <div class="skill-block-title">已启用的平台技能</div>
              <el-table
                v-if="skillMounts.length"
                :data="skillMounts"
                border
                size="small"
                class="config-table"
                style="margin-bottom: 12px"
              >
                <el-table-column prop="name" label="name" min-width="140" />
                <el-table-column prop="description" label="description" min-width="280">
                  <template #default="{ row }">
                    <div class="skill-desc-readonly">{{ row.description || '—' }}</div>
                  </template>
                </el-table-column>
                <el-table-column label="version" width="100">
                  <template #default="{ row }">{{ row.version || '—' }}</template>
                </el-table-column>
                <el-table-column label="来源" width="90" align="center">
                  <template #default>
                    <el-tag size="small" type="success" effect="plain">platform</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="" width="100" align="center" fixed="right">
                  <template #default="{ row }">
                    <el-button size="small" link type="danger" @click="unmountPlatform(row)">
                      取消启用
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <div v-else class="hint" style="margin-bottom: 12px">尚未启用平台技能。</div>

              <div v-if="unmountedPlatformSkills.length" class="skill-mount-picker">
                <span class="hint">从平台库启用：</span>
                <el-button
                  v-for="pkg in unmountedPlatformSkills"
                  :key="pkg.name"
                  size="small"
                  :loading="mountBusy === pkg.name"
                  @click="mountPlatform(pkg.name)"
                >
                  {{ pkg.name }}
                </el-button>
              </div>

              <div class="skill-block-title" style="margin-top: 16px">本 Agent 技能包</div>
              <el-table
                v-if="skillPackages.length"
                :data="skillPackages"
                border
                size="small"
                class="config-table"
              >
                <el-table-column prop="name" label="name" min-width="140" />
                <el-table-column label="description" min-width="320">
                  <template #default="{ row }">
                    <el-input
                      v-model="descDrafts[row.name]"
                      type="textarea"
                      :autosize="{ minRows: 2, maxRows: 10 }"
                      placeholder="进入 prompt 的触发说明"
                    />
                    <div class="skill-desc-actions">
                      <el-button
                        type="primary"
                        link
                        size="small"
                        :icon="Check"
                        :disabled="!descriptionDirty(row)"
                        :loading="descSaving === row.name"
                        @click="savePackageDescription(row)"
                      >
                        保存描述
                      </el-button>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="version" width="120">
                  <template #default="{ row }">
                    <div>{{ row.version || '—' }}</div>
                    <div class="hint" style="margin: 2px 0 0">
                      {{ formatPackageUpdatedAt(row.updated_at) }}
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="文件" width="72" align="center">
                  <template #default="{ row }">{{ row.file_count }}</template>
                </el-table-column>
                <el-table-column label="脚本" width="72" align="center">
                  <template #default="{ row }">
                    {{ packageScriptCount(row) }}
                  </template>
                </el-table-column>
                <el-table-column label="" width="108" align="center" fixed="right">
                  <template #default="{ row }">
                    <div class="skill-row-actions">
                      <el-upload
                        :show-file-list="false"
                        accept=".zip,application/zip"
                        :http-request="(opts: UploadRequestOptions) => updatePackageRequest(row, opts)"
                        :disabled="updatingPackageName === row.name"
                      >
                        <el-button
                          :icon="Refresh"
                          circle
                          size="small"
                          :loading="updatingPackageName === row.name"
                          title="更新 zip"
                        />
                      </el-upload>
                      <el-button
                        :icon="Delete"
                        circle
                        size="small"
                        @click="removePackage(row)"
                      />
                    </div>
                  </template>
                </el-table-column>
              </el-table>
              <div v-else class="hint">尚无本 Agent 包。可上传 zip，或仅启用平台技能。</div>
            </div>
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

          <section data-section="limits" class="editor-section">
            <h3 class="editor-section__title">运行限制</h3>
            <div class="hint" style="margin-bottom: 10px">
              按「单条用户消息」计数：工具轮次与 token 预算在该 Run 内累计，等待审批 / 追问恢复后不会重置。
            </div>
            <el-form label-width="140px">
              <el-form-item label="工具轮次上限">
                <el-input-number v-model="form.runtime_max_rounds" :min="1" :max="32" />
                <span class="hint inline">单 Run 内带 tools 的模型调用次数（1–32）</span>
              </el-form-item>
              <el-form-item label="达上限后">
                <el-select v-model="form.runtime_on_max_rounds" style="width: 200px">
                  <el-option label="收尾总结（不再调工具）" value="summarize" />
                  <el-option label="直接停止" value="stop" />
                </el-select>
              </el-form-item>
              <el-form-item label="Token 预算">
                <el-input-number
                  v-model="form.runtime_max_run_tokens"
                  :min="0"
                  :step="10000"
                  :controls="false"
                  style="width: 160px"
                />
                <span class="hint inline">单 Run 累计 tokens；0 = 用服务端默认</span>
              </el-form-item>
              <el-form-item label="并行执行工具">
                <el-switch v-model="form.runtime_parallel" />
                <span class="hint inline">
                  同一轮多个 client / remote_http 工具并发执行（最多 4 个）；builtin 与待审批仍串行
                </span>
              </el-form-item>
              <el-form-item label="单工具超时（秒）">
                <el-input-number
                  v-model="form.runtime_tool_timeout"
                  :min="1"
                  :max="3600"
                  :step="5"
                />
              </el-form-item>
              <el-form-item label="单 Run 超时（秒）">
                <el-input-number
                  v-model="form.runtime_run_timeout"
                  :min="5"
                  :max="86400"
                  :step="30"
                />
              </el-form-item>
            </el-form>
          </section>

          <section data-section="theme" class="editor-section">
            <h3 class="editor-section__title">主题样式</h3>
            <div class="hint" style="margin-bottom: 10px">
              配置嵌入聊天页颜色 / 圆角（写入 Agent
              <code>ui_theme</code>，映射为
              <code>--af-*</code>
              CSS 变量）。留空则用默认浅色主题。
            </div>
            <div class="theme-presets">
              <el-button size="small" @click="applyThemePreset('light')">浅色预设</el-button>
              <el-button size="small" @click="applyThemePreset('dark')">深色预设</el-button>
              <el-button size="small" link type="danger" @click="clearTheme">清除主题</el-button>
            </div>
            <div v-for="group in THEME_GROUPS" :key="group.id" class="theme-group">
              <div class="theme-group__title">{{ group.label }}</div>
              <div class="theme-grid">
                <div
                  v-for="field in themeFieldsOf(group.id)"
                  :key="field.key"
                  class="theme-field"
                >
                  <div class="theme-field__label">{{ field.label }}</div>
                  <div class="theme-field__row">
                    <el-color-picker
                      v-if="field.kind === 'color'"
                      :model-value="themeColorModel(field.key).value || null"
                      show-alpha
                      @update:model-value="
                        (v: string | null) => {
                          themeColorModel(field.key).value = v || ''
                        }
                      "
                    />
                    <el-input
                      :model-value="themeColorModel(field.key).value"
                      :placeholder="
                        field.kind === 'color' ? '#rrggbb / rgba() / transparent' : '16px'
                      "
                      clearable
                      @update:model-value="
                        (v: string) => {
                          themeColorModel(field.key).value = v
                        }
                      "
                    />
                  </div>
                </div>
              </div>
            </div>
            <div
              class="theme-preview"
              :style="{
                background: form.ui_theme.bg || '#f7f7f8',
                color: form.ui_theme.text || '#0d0d0d',
                borderColor: form.ui_theme.border || '#e5e5e5',
              }"
            >
              <div class="theme-preview__muted" :style="{ color: form.ui_theme.muted || '#8e8e8e' }">
                预览 · {{ form.ui_theme.preset || 'custom' }}
              </div>
              <div
                class="theme-preview__bubble"
                :style="{
                  background: form.ui_theme.userBubble || '#f4f4f4',
                  color: form.ui_theme.userBubbleText || form.ui_theme.text || '#0d0d0d',
                  border: `1px solid ${form.ui_theme.userBubbleBorder || 'transparent'}`,
                  borderRadius: form.ui_theme.radiusBubble || form.ui_theme.radiusMd || '16px',
                }"
              >
                用户消息
              </div>
              <div
                class="theme-preview__assistant"
                :style="{
                  background: form.ui_theme.assistantBubble || 'transparent',
                  color: form.ui_theme.assistantBubbleText || form.ui_theme.text || '#0d0d0d',
                  border: `1px solid ${form.ui_theme.assistantBubbleBorder || 'transparent'}`,
                  borderRadius: form.ui_theme.radiusBubble || '16px',
                }"
              >
                助手消息
              </div>
              <div
                class="theme-preview__composer"
                :style="{
                  background: form.ui_theme.composerCard || form.ui_theme.surface || '#fff',
                  color: form.ui_theme.composerText || form.ui_theme.text || '#0d0d0d',
                  border: `1px solid ${form.ui_theme.composerBorder || form.ui_theme.border || '#e5e5e5'}`,
                }"
              >
                <span :style="{ color: form.ui_theme.composerMuted || '#a3a3a3' }">输入框占位…</span>
                <span
                  class="theme-preview__send"
                  :style="{
                    background: form.ui_theme.composerSendBg || '#0d0d0d',
                    color: form.ui_theme.composerSendText || '#fff',
                  }"
                >
                  发送
                </span>
              </div>
            </div>
          </section>
        </div>
      </div>

      <div class="editor-actions">
        <el-button @click="goBack">返回</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </div>
    </el-card>

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
        <el-button type="primary" @click="copyCreds">复制 AK/SK</el-button>
        <el-button @click="credVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.hint {
  color: var(--af-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}
.hint.inline {
  margin-left: 8px;
}
.skill-desc-actions {
  margin-top: 4px;
}
.skill-row-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.skill-block-title {
  font-size: 13px;
  font-weight: 650;
  color: #0f172a;
  margin-bottom: 8px;
}
.skill-mount-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
}
.skill-desc-readonly {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.45;
  font-size: 13px;
  color: #334155;
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

.editor-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.editor-section__head .editor-section__title {
  margin: 0;
}
.editor-section__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 20px;
  margin-left: 8px;
  padding: 0 6px;
  border-radius: 999px;
  background: #e2e8f0;
  color: #475569;
  font-size: 12px;
  font-weight: 600;
  vertical-align: middle;
}
.config-table {
  width: 100%;
}
.config-table :deep(.el-input__wrapper),
.config-table :deep(.el-textarea__inner) {
  box-shadow: none;
  background: transparent;
}
.config-table :deep(.el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}

.theme-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.theme-group {
  margin-bottom: 14px;
}
.theme-group__title {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 8px;
}
.theme-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px 14px;
  margin-bottom: 4px;
}
.theme-preview__assistant {
  align-self: flex-start;
  padding: 8px 12px;
  font-size: 13px;
  max-width: 80%;
}
.theme-preview__composer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 14px;
  font-size: 13px;
}
.theme-preview__send {
  flex-shrink: 0;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}
.theme-field__label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 4px;
}
.theme-field__row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.theme-field__row .el-input {
  flex: 1;
  min-width: 0;
}
.theme-preview {
  border: 1px solid;
  border-radius: 14px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.theme-preview__muted {
  font-size: 12px;
}
.theme-preview__bubble {
  align-self: flex-end;
  padding: 8px 12px;
  font-size: 13px;
  max-width: 70%;
}
.theme-preview__accent {
  font-size: 13px;
  font-weight: 600;
}

.agent-edit-page {
  min-height: 100%;
}
.editor-card {
  padding: 0;
}
.editor-card :deep(.el-card__body) {
  padding: 0;
}
.editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 16px 16px;
  background: #eef2f7;
}
.agent-editor {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 12px;
  height: calc(100vh - 220px);
  min-height: 480px;
  padding: 12px;
  border-radius: 0;
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

