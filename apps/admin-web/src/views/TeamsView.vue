<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, type Agent, type AgentTeam, type TeamMember } from '../api/client'
import PageHeader from '../components/PageHeader.vue'

const agents = ref<Agent[]>([])
const teams = ref<AgentTeam[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const editingId = ref<string | null>(null)

const form = reactive({
  name: '',
  lead_agent_id: '',
  members: [] as TeamMember[],
  max_depth: 1,
  max_parallel: 3,
  max_child_rounds: 8,
  result_max_chars: 4000,
  allow_async: true,
  enabled: true,
})

const agentName = computed(() => {
  const map = new Map(agents.value.map((a) => [a.id, a.name]))
  return (id: string) => map.get(id) || id.slice(0, 8)
})

const memberCandidates = computed(() =>
  agents.value.filter((a) => a.id !== form.lead_agent_id),
)

function resetForm() {
  editingId.value = null
  form.name = ''
  form.lead_agent_id = agents.value[0]?.id || ''
  form.members = []
  form.max_depth = 1
  form.max_parallel = 3
  form.max_child_rounds = 8
  form.result_max_chars = 4000
  form.allow_async = true
  form.enabled = true
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: AgentTeam) {
  editingId.value = row.id
  form.name = row.name
  form.lead_agent_id = row.lead_agent_id
  form.members = row.members.map((m) => ({ ...m }))
  form.max_depth = row.max_depth
  form.max_parallel = row.max_parallel
  form.max_child_rounds = row.max_child_rounds
  form.result_max_chars = row.result_max_chars
  form.allow_async = row.allow_async
  form.enabled = row.enabled
  dialogVisible.value = true
}

function addMember() {
  const first = memberCandidates.value.find(
    (a) => !form.members.some((m) => m.agent_id === a.id),
  )
  if (!first) {
    ElMessage.warning('没有可添加的成员 Agent')
    return
  }
  form.members.push({ agent_id: first.id, role: '' })
}

function removeMember(index: number) {
  form.members.splice(index, 1)
}

async function refresh() {
  loading.value = true
  try {
    const [agentList, teamList] = await Promise.all([api.listAgents(), api.listTeams()])
    agents.value = agentList
    teams.value = teamList
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.error('请填写团队名称')
    return
  }
  if (!form.lead_agent_id) {
    ElMessage.error('请选择负责人 Agent')
    return
  }
  const body = {
    name: form.name.trim(),
    lead_agent_id: form.lead_agent_id,
    members: form.members
      .filter((m) => m.agent_id)
      .map((m) => ({ agent_id: m.agent_id, role: (m.role || '').trim() })),
    max_depth: form.max_depth,
    max_parallel: form.max_parallel,
    max_child_rounds: form.max_child_rounds,
    result_max_chars: form.result_max_chars,
    allow_async: form.allow_async,
    enabled: form.enabled,
  }
  try {
    if (editingId.value) {
      await api.updateTeam(editingId.value, body)
      ElMessage.success('已更新团队')
    } else {
      await api.createTeam(body)
      ElMessage.success('已创建团队')
    }
    dialogVisible.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function removeTeam(row: AgentTeam) {
  try {
    await ElMessageBox.confirm(`删除团队「${row.name}」？`, '确认', { type: 'warning' })
    await api.deleteTeam(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <div>
    <PageHeader
      title="多 Agent 团队"
      description="配置负责人 + 成员；在 Lead Agent 编辑页绑定 team_id 后即可 agent_delegate"
    >
      <template #actions>
        <el-button :icon="Refresh" @click="refresh">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建团队</el-button>
      </template>
    </PageHeader>

    <el-table v-loading="loading" :data="teams" stripe>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="负责人" min-width="140">
        <template #default="{ row }">{{ agentName(row.lead_agent_id) }}</template>
      </el-table-column>
      <el-table-column label="成员" min-width="220">
        <template #default="{ row }">
          <el-tag
            v-for="m in row.members"
            :key="m.agent_id"
            size="small"
            class="member-tag"
          >
            {{ m.role || 'member' }}: {{ agentName(m.agent_id) }}
          </el-tag>
          <span v-if="!row.members.length" class="hint">无</span>
        </template>
      </el-table-column>
      <el-table-column label="深度/并行" width="110">
        <template #default="{ row }">{{ row.max_depth }} / {{ row.max_parallel }}</template>
      </el-table-column>
      <el-table-column label="异步" width="80">
        <template #default="{ row }">
          <el-tag :type="row.allow_async ? 'success' : 'info'" size="small">
            {{ row.allow_async ? '开' : '关' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
            {{ row.enabled ? '是' : '否' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="removeTeam(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑团队' : '新建团队'"
      width="640px"
      destroy-on-close
    >
      <el-form label-width="120px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="例如：研发协作小队" />
        </el-form-item>
        <el-form-item label="负责人" required>
          <el-select v-model="form.lead_agent_id" filterable style="width: 100%">
            <el-option
              v-for="a in agents"
              :key="a.id"
              :label="a.name"
              :value="a.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="成员">
          <div class="members">
            <div v-for="(m, idx) in form.members" :key="idx" class="members__row">
              <el-select v-model="m.agent_id" filterable style="flex: 1">
                <el-option
                  v-for="a in memberCandidates"
                  :key="a.id"
                  :label="a.name"
                  :value="a.id"
                />
              </el-select>
              <el-input v-model="m.role" placeholder="role: pm/dev/qa" style="width: 140px" />
              <el-button link type="danger" @click="removeMember(idx)">移除</el-button>
            </div>
            <el-button size="small" @click="addMember">添加成员</el-button>
          </div>
        </el-form-item>
        <el-form-item label="最大深度">
          <el-input-number v-model="form.max_depth" :min="1" :max="3" />
        </el-form-item>
        <el-form-item label="最大并行">
          <el-input-number v-model="form.max_parallel" :min="1" :max="8" />
        </el-form-item>
        <el-form-item label="子轮次上限">
          <el-input-number v-model="form.max_child_rounds" :min="1" :max="32" />
        </el-form-item>
        <el-form-item label="回灌字数">
          <el-input-number v-model="form.result_max_chars" :min="200" :max="20000" :step="200" />
        </el-form-item>
        <el-form-item label="允许异步">
          <el-switch v-model="form.allow_async" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.member-tag {
  margin: 0 4px 4px 0;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.members {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.members__row {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
