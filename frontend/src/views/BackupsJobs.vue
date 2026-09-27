<template>
  <div v-loading="loading">
    <div class="toolbar">
      <h2 class="page-title">备份任务</h2>
      <div>
        <el-button type="primary" :loading="triggering === 'full'" :disabled="running" @click="trigger('full')">全量备份</el-button>
        <el-button :loading="triggering === 'incremental'" :disabled="running" @click="trigger('incremental')">增量备份</el-button>
        <el-button :icon="Refresh" circle @click="loadAll" />
      </div>
    </div>

    <el-card v-if="retention" shadow="never" class="schedule-card mb-3">
      <div class="schedule-row">
        <div class="schedule-item">
          <span class="schedule-label">全量定时</span>
          <el-tag :type="retention.full_backup_enabled ? 'success' : 'info'" size="small">
            {{ retention.full_backup_enabled ? describeSchedule(retention.full_backup_schedule) : '已禁用' }}
          </el-tag>
        </div>
        <div class="schedule-item">
          <span class="schedule-label">增量定时</span>
          <el-tag :type="retention.incremental_backup_enabled ? 'success' : 'info'" size="small">
            {{ retention.incremental_backup_enabled ? describeSchedule(retention.incremental_backup_schedule) : '已禁用' }}
          </el-tag>
        </div>
        <div class="schedule-hint">在「备份文件 → 备份策略」中修改周期，保存后自动生效</div>
      </div>
    </el-card>

    <el-alert v-if="running" type="info" title="有任务正在运行，页面将自动刷新" show-icon class="mb-3" />
    <el-table :data="items" stripe>
      <el-table-column prop="id" label="ID" width="80">
        <template #default="{ row }">#{{ row.id }}</template>
      </el-table-column>
      <el-table-column prop="backup_type_display" label="类型" width="100" />
      <el-table-column prop="status_display" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="trigger_display" label="触发" width="80" />
      <el-table-column label="存储结果" min-width="160">
        <template #default="{ row }">
          <el-tag
            v-for="(result, name) in row.storage_results"
            :key="name"
            size="small"
            :type="result.ok ? 'success' : 'danger'"
            class="mr-1"
          >{{ name }}</el-tag>
          <span v-if="!Object.keys(row.storage_results || {}).length">-</span>
        </template>
      </el-table-column>
      <el-table-column label="开始" width="170" class-name="time-col">
        <template #default="{ row }">
          <span class="time-cell">{{ formatDateTime(row.started_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="结束" width="170" class-name="time-col">
        <template #default="{ row }">
          <span class="time-cell">{{ formatDateTime(row.finished_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="耗时" width="80">
        <template #default="{ row }">{{ row.duration_seconds ? row.duration_seconds + 's' : '-' }}</template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { backupApi } from '@/api'
import { formatDateTime } from '@/utils/datetime'
import { describeCron as describeSchedule } from '@/utils/cronSchedule'

const loading = ref(false)
const triggering = ref('')
const running = ref(false)
const items = ref([])
const retention = ref(null)
let timer = null

function statusType(status) {
  return { success: 'success', failed: 'danger', partial: 'warning', running: 'primary' }[status] || 'info'
}

async function load() {
  loading.value = true
  try {
    const { data } = await backupApi.jobs()
    items.value = data.items
    running.value = data.running
  } finally {
    loading.value = false
  }
}

async function loadRetention() {
  try {
    const { data } = await backupApi.retention()
    retention.value = data
  } catch {
    retention.value = null
  }
}

async function loadAll() {
  await Promise.all([load(), loadRetention()])
}

async function trigger(type) {
  triggering.value = type
  try {
    await backupApi.trigger(type)
    ElMessage.success(`已提交${type === 'full' ? '全量' : '增量'}备份任务`)
    await load()
  } finally {
    triggering.value = ''
  }
}

onMounted(() => {
  loadAll()
  timer = setInterval(load, 15000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-title { margin: 0; }
.mb-3 { margin-bottom: 16px; }
.mr-1 { margin-right: 4px; }
.schedule-card { font-size: 13px; }
.schedule-row { display: flex; flex-wrap: wrap; align-items: center; gap: 16px 24px; }
.schedule-item { display: flex; align-items: center; gap: 8px; }
.schedule-label { color: #606266; }
.schedule-hint { color: #909399; font-size: 12px; }
.time-cell { white-space: nowrap; }
:deep(.time-col .cell) { white-space: nowrap; }
</style>
