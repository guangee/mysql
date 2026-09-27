<template>
  <div v-loading="loading">
    <div class="page-header">
      <h2 class="page-title">MySQL 参数</h2>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert
      v-if="overview.restart_required"
      type="warning"
      show-icon
      :closable="false"
      class="mb-4"
      title="部分参数已写入配置文件，需重启 MySQL 容器后生效"
    />

    <el-alert
      v-if="error"
      type="error"
      show-icon
      :closable="false"
      class="mb-4"
      :title="error"
    />

    <template v-for="group in overview.groups" :key="group.key">
      <el-card shadow="never" class="group-card">
        <template #header>
          <div class="card-header">
            <span>{{ group.label }}</span>
            <el-button
              type="primary"
              size="small"
              :loading="saving === group.key"
              :disabled="!hasChanges(group)"
              @click="saveGroup(group)"
            >保存本组</el-button>
          </div>
        </template>

        <div v-for="item in group.items" :key="item.key" class="param-row">
          <div class="param-info">
            <div class="param-label">
              {{ item.label }}
              <el-tag v-if="item.dynamic" size="small" type="success">即时生效</el-tag>
              <el-tag v-else size="small" type="warning">需重启</el-tag>
            </div>
            <div class="param-desc">{{ item.description }}</div>
            <div v-if="item.usage" class="param-usage">
              <template v-if="item.key === 'max_connections'">
                当前 {{ item.usage.current }} 连接，占用 {{ item.usage.percent }}%
              </template>
              <template v-else-if="item.key === 'innodb_buffer_pool_size'">
                已用 {{ formatSize(item.usage.used_bytes) }}，占用 {{ item.usage.percent }}%
              </template>
              <el-progress
                v-if="item.usage.percent != null"
                :percentage="Math.min(item.usage.percent, 100)"
                :stroke-width="6"
                :status="item.usage.percent >= 85 ? 'exception' : undefined"
                class="usage-bar"
              />
            </div>
          </div>

          <div class="param-control">
            <div class="control-row">
              <template v-if="item.unit === 'bytes'">
                <el-input-number
                  v-model="draft[item.key]"
                  :min="bytesMinMB(item)"
                  :max="bytesMaxMB(item)"
                  :step="bytesStepMB(item)"
                  controls-position="right"
                />
                <span class="unit">MB</span>
              </template>
              <template v-else-if="item.unit === 'seconds'">
                <el-input-number
                  v-model="draft[item.key]"
                  :min="item.min"
                  :max="item.max"
                  :step="item.step || 1"
                  controls-position="right"
                />
                <span class="unit">秒</span>
              </template>
              <template v-else>
                <el-input-number
                  v-model="draft[item.key]"
                  :min="item.min"
                  :max="item.max"
                  :step="item.step || 1"
                  controls-position="right"
                />
              </template>
            </div>
            <div v-if="item.persisted_value != null && item.persisted_value !== item.value" class="persisted-hint">
              配置文件: {{ displayValue(item, item.persisted_value) }}
            </div>
          </div>
        </div>
      </el-card>
    </template>

    <div v-if="overview.config_file" class="config-hint">
      持久化文件：<code>{{ overview.config_file }}</code>
      <span v-if="overview.chunk_size_bytes">（缓冲池调整步长 {{ formatSize(overview.chunk_size_bytes) }}）</span>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { mysqlApi } from '@/api'

const loading = ref(false)
const saving = ref('')
const error = ref('')
const overview = reactive({ groups: [], restart_required: false, config_file: '', chunk_size_bytes: 0 })
const draft = reactive({})
const original = reactive({})

function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return (bytes / 1024 ** 3).toFixed(2) + ' GB'
}

function bytesStepMB(item) {
  return Math.max((item.step || overview.chunk_size_bytes || 134217728) / 1024 / 1024, 1)
}

function bytesMinMB(item) {
  return Math.ceil(item.min / 1024 / 1024)
}

function bytesMaxMB(item) {
  return item.max ? Math.floor(item.max / 1024 / 1024) : 1024 * 16
}

function displayValue(item, value) {
  if (item.unit === 'bytes') return formatSize(value)
  if (item.unit === 'seconds') return `${value} 秒`
  return String(value)
}

function toDraftValue(item) {
  if (item.unit === 'bytes') return Math.round(item.value / 1024 / 1024)
  return item.value
}

function toApiValue(item, draftVal) {
  if (item.unit === 'bytes') return Math.round(draftVal * 1024 * 1024)
  return draftVal
}

function syncDraft(data) {
  Object.keys(draft).forEach((k) => delete draft[k])
  Object.keys(original).forEach((k) => delete original[k])
  for (const group of data.groups || []) {
    for (const item of group.items) {
      const v = toDraftValue(item)
      draft[item.key] = v
      original[item.key] = v
    }
  }
}

function hasChanges(group) {
  return group.items.some((item) => draft[item.key] !== original[item.key])
}

function collectGroupChanges(group) {
  const settings = {}
  for (const item of group.items) {
    if (draft[item.key] !== original[item.key]) {
      settings[item.key] = toApiValue(item, draft[item.key])
    }
  }
  return settings
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await mysqlApi.settings()
    Object.assign(overview, data)
    syncDraft(data)
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function saveGroup(group) {
  const settings = collectGroupChanges(group)
  if (!Object.keys(settings).length) return

  const hasRestart = group.items.some((item) => !item.dynamic && settings[item.key] != null)
  if (hasRestart) {
    await ElMessageBox.confirm(
      '本组包含需重启 MySQL 才能生效的参数，是否继续保存？',
      '确认保存',
      { type: 'warning' },
    )
  }

  saving.value = group.key
  try {
    const { data } = await mysqlApi.updateSettings({ settings })
    Object.assign(overview, data.overview || {})
    syncDraft(data.overview || overview)
    if (data.restart_required) {
      ElMessage.success('已保存；部分参数需重启 MySQL 后生效')
    } else {
      ElMessage.success('参数已保存并生效')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.page-title { margin: 0; }
.mb-4 { margin-bottom: 16px; }
.group-card { margin-bottom: 20px; }
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.param-row {
  display: flex;
  gap: 24px;
  padding: 16px 0;
  border-bottom: 1px solid #ebeef5;
}
.param-row:last-child { border-bottom: none; }
.param-info { flex: 1; min-width: 0; }
.param-label {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.param-desc { font-size: 13px; color: #909399; line-height: 1.5; }
.param-usage { margin-top: 8px; font-size: 12px; color: #606266; }
.usage-bar { max-width: 280px; margin-top: 6px; }
.param-control {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.control-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}
.unit { margin-left: 8px; color: #909399; font-size: 13px; white-space: nowrap; }
.persisted-hint { font-size: 12px; color: #909399; }
.config-hint { font-size: 13px; color: #909399; margin-top: 8px; }
</style>
