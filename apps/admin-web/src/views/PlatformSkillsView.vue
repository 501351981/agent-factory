<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Delete, Refresh, Upload } from '@element-plus/icons-vue'
import type { UploadRequestOptions } from 'element-plus'
import { api, type PlatformSkillPackage } from '../api/client'
import PageHeader from '../components/PageHeader.vue'

const packages = ref<PlatformSkillPackage[]>([])
const loading = ref(false)
const uploading = ref(false)
const descDrafts = reactive<Record<string, string>>({})
const descSaving = ref<string | null>(null)

function syncDescDrafts(rows: PlatformSkillPackage[]) {
  for (const key of Object.keys(descDrafts)) {
    if (!rows.some((p) => p.name === key)) delete descDrafts[key]
  }
  for (const pkg of rows) {
    if (!(pkg.name in descDrafts)) descDrafts[pkg.name] = pkg.description || ''
  }
}

async function refresh() {
  loading.value = true
  try {
    packages.value = await api.listPlatformSkills()
    syncDescDrafts(packages.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function descriptionDirty(pkg: PlatformSkillPackage) {
  return (descDrafts[pkg.name] ?? '') !== (pkg.description || '')
}

function formatUpdatedAt(value: string) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

async function saveDescription(pkg: PlatformSkillPackage) {
  const next = (descDrafts[pkg.name] ?? '').trimEnd()
  descSaving.value = pkg.name
  try {
    const updated = await api.updatePlatformSkill(pkg.name, { description: next })
    const idx = packages.value.findIndex((p) => p.name === pkg.name)
    if (idx >= 0) packages.value[idx] = updated
    descDrafts[pkg.name] = updated.description || ''
    ElMessage.success('描述已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    descSaving.value = null
  }
}

async function uploadRequest(options: UploadRequestOptions) {
  const raw = options.file as File
  if (!raw.name.toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请上传 .zip 技能包')
    return
  }
  uploading.value = true
  try {
    const installed = await api.uploadPlatformSkill(raw)
    ElMessage.success(`已安装平台技能 ${installed.name}`)
    if (installed.warnings?.length) ElMessage.warning(installed.warnings.join('；'))
    options.onSuccess?.(installed)
    await refresh()
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    if (message.includes('already installed') || message.includes('skill_exists')) {
      try {
        await ElMessageBox.confirm(
          '同名平台技能已存在。覆盖后只保留当前一份。是否继续？',
          '覆盖更新',
          { type: 'warning', confirmButtonText: '覆盖', cancelButtonText: '取消' },
        )
        let keepDescription = false
        try {
          await ElMessageBox.confirm(
            '是否保留当前 description？',
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
        const installed = await api.uploadPlatformSkill(raw, {
          force: true,
          keepDescription,
        })
        ElMessage.success(`已覆盖 ${installed.name}`)
        options.onSuccess?.(installed)
        await refresh()
        return
      } catch {
        return
      }
    }
    ElMessage.error(message)
    options.onError?.(err as never)
  } finally {
    uploading.value = false
  }
}

async function removePackage(pkg: PlatformSkillPackage) {
  try {
    await ElMessageBox.confirm(
      `删除平台技能「${pkg.name}」？将从所有已启用的 Agent 卸载。`,
      '删除平台技能',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await api.deletePlatformSkill(pkg.name)
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
      title="平台技能"
      description="全厂技能库（如 skill-creator）。在 Agent 编辑页启用后才会进入该 Agent 的 prompt catalog。"
    >
      <template #actions>
        <el-button :icon="Refresh" @click="refresh">刷新</el-button>
        <el-upload
          :show-file-list="false"
          accept=".zip,application/zip"
          :http-request="uploadRequest"
          :disabled="uploading"
        >
          <el-button type="primary" :icon="Upload" :loading="uploading">上传 zip</el-button>
        </el-upload>
      </template>
    </PageHeader>

    <el-card shadow="never" v-loading="loading">
      <el-table v-if="packages.length" :data="packages" border size="small">
        <el-table-column prop="name" label="name" min-width="140" />
        <el-table-column label="description" min-width="320">
          <template #default="{ row }">
            <el-input
              v-model="descDrafts[row.name]"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 10 }"
            />
            <div style="margin-top: 4px">
              <el-button
                type="primary"
                link
                size="small"
                :icon="Check"
                :disabled="!descriptionDirty(row)"
                :loading="descSaving === row.name"
                @click="saveDescription(row)"
              >
                保存描述
              </el-button>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="version" width="120">
          <template #default="{ row }">
            <div>{{ row.version || '—' }}</div>
            <div class="muted">{{ formatUpdatedAt(row.updated_at) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.source === 'builtin' ? 'success' : 'info'">
              {{ row.source }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用 Agent" width="100" align="center">
          <template #default="{ row }">{{ row.mount_count }}</template>
        </el-table-column>
        <el-table-column label="文件" width="72" align="center">
          <template #default="{ row }">{{ row.file_count }}</template>
        </el-table-column>
        <el-table-column label="" width="72" align="center" fixed="right">
          <template #default="{ row }">
            <el-button :icon="Delete" circle size="small" @click="removePackage(row)" />
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="muted">暂无平台技能。启动服务会自动种子 skill-creator；也可手动上传 zip。</div>
    </el-card>
  </div>
</template>

<style scoped>
.muted {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}
</style>
