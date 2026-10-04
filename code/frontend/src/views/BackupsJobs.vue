<template>
  <div v-loading="loading">
    <div class="toolbar">
      <h2 v-if="!embedded" class="page-title">备份任务</h2>
      <div v-else class="toolbar-spacer" />
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
        <div class="schedule-hint">在「备份文件」页签的「备份策略」中修改周期，保存后自动生效</div>
      </div>
    </el-card>

    <el-alert v-if="running" type="info" title="有任务正在运行，进度会自动更新" show-icon class="mb-3" />
    <el-table :data="items" stripe row-key="id">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="job-detail">
            <div v-if="row.progress?.filename">文件：<code>{{ row.progress.filename }}</code></div>
            <div v-if="row.progress?.databases">数据库：{{ row.progress.databases }}</div>
            <div v-if="row.progress?.detail" class="detail-line">当前：{{ row.progress.detail }}</div>
            <div v-if="storageEntries(row).length" class="storage-lines">
              <div v-for="item in storageEntries(row)" :key="item.name">
                {{ item.name }}：{{ item.ok ? '已上传并校验' : '上传失败' }}
                <span v-if="item.message">（{{ item.message }}）</span>
              </div>
            </div>
            <div v-if="row.error_message" class="error-line">{{ row.error_message }}</div>
            <pre v-if="row.progress?.recent_lines?.length" class="recent-log">{{ row.progress.recent_lines.join('\n') }}</pre>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="id" label="ID" width="70">
        <template #default="{ row }">#{{ row.id }}</template>
      </el-table-column>
      <el-table-column prop="backup_type_display" label="类型" width="80" />
      <el-table-column prop="status_display" label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="160">
        <template #default="{ row }">
          <div class="progress-cell">
            <div class="progress-label">{{ row.progress?.stage_label || '-' }}</div>
            <el-progress
              :percentage="Math.min(Number(row.progress?.percent) || 0, 100)"
              :stroke-width="14"
              :status="progressStatus(row)"
            />
          </div>
        </template>
      </el-table-column>
      <el-table-column label="文件大小" width="110">
        <template #default="{ row }">{{ row.progress?.size_display || '-' }}</template>
      </el-table-column>
      <el-table-column label="云存储" min-width="180">
        <template #default="{ row }">
          <el-tag size="small" :type="uploadTagType(row.progress?.upload_status)">
            {{ row.progress?.upload_label || '-' }}
          </el-tag>
          <el-tag
            v-for="item in storageEntries(row)"
            :key="item.name"
            size="small"
            :type="item.ok ? 'success' : 'danger'"
            class="ml-1"
          >{{ item.name }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="trigger_display" label="触发" width="70" />
      <el-table-column label="开始" width="170" class-name="time-col">
        <template #default="{ row }">
          <span class="time-cell">{{ formatDateTime(row.started_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="耗时" width="80">
        <template #default="{ row }">{{ row.duration_seconds ? row.duration_seconds + 's' : '-' }}</template>
      </el-table-column>
    </el-table>

    <div v-if="total > 0" class="pagination-bar">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        small
        @current-change="load()"
        @size-change="onPageSizeChange"
      />
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { backupApi } from '@/api'
import { formatDateTime } from '@/utils/datetime'
import { describeCron as describeSchedule } from '@/utils/cronSchedule'

defineProps({
  embedded: { type: Boolean, default: false },
})

const loading = ref(false)
const triggering = ref('')
const running = ref(false)
const items = ref([])
const retention = ref(null)
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
let timer = null

function statusType(status) {
  return { success: 'success', failed: 'danger', partial: 'warning', running: 'primary' }[status] || 'info'
}

function progressStatus(row) {
  if (row.status === 'success') return 'success'
  if (row.status === 'failed') return 'exception'
  return undefined
}

function uploadTagType(status) {
  return {
    uploaded: 'success',
    partial: 'warning',
    failed: 'danger',
    uploading: 'primary',
    skipped: 'info',
    pending: 'info',
  }[status] || 'info'
}

function storageEntries(row) {
  const storages = row.progress?.storages || row.storage_results || {}
  return Object.entries(storages).map(([name, result]) => ({
    name,
    ok: !!result?.ok,
    message: result?.message || '',
  }))
}

function scheduleRefresh() {
  if (timer) clearInterval(timer)
  timer = setInterval(() => load(true), running.value ? 2000 : 15000)
}

function onPageSizeChange() {
  page.value = 1
  load()
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const { data } = await backupApi.jobs({ page: page.value, pageSize: pageSize.value })
    items.value = data.items || []
    total.value = Number(data.total) || 0
    if (data.page) page.value = data.page
    const wasRunning = running.value
    running.value = data.running
    // 当前页无数据且非第一页时回退一页
    if (!items.value.length && page.value > 1 && total.value > 0) {
      page.value = Math.max(1, Math.ceil(total.value / pageSize.value))
      await load(silent)
      return
    }
    if (wasRunning !== data.running || !timer) scheduleRefresh()
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
    page.value = 1
    await load()
  } finally {
    triggering.value = ''
  }
}

onMounted(() => {
  loadAll()
  scheduleRefresh()
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.toolbar-spacer { flex: 1; }
.page-title { margin: 0; }
.pagination-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.mb-3 { margin-bottom: 16px; }
.mr-1 { margin-right: 4px; }
.schedule-card { font-size: 13px; }
.schedule-row { display: flex; flex-wrap: wrap; align-items: center; gap: 16px 24px; }
.schedule-item { display: flex; align-items: center; gap: 8px; }
.schedule-label { color: #606266; }
.schedule-hint { color: #909399; font-size: 12px; }
.time-cell { white-space: nowrap; }
:deep(.time-col .cell) { white-space: nowrap; }
:deep(.el-progress-bar__inner) { transition: none; }
.progress-cell { width: 120px; }
.progress-label { margin-bottom: 4px; font-size: 12px; color: #606266; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.progress-cell :deep(.el-progress__text) { min-width: 36px; font-size: 12px !important; }
.ml-1 { margin-left: 4px; }
.job-detail { padding: 4px 12px 8px 48px; color: #606266; font-size: 13px; line-height: 1.7; }
.detail-line { color: #909399; }
.error-line { color: #f56c6c; white-space: pre-wrap; }
.recent-log {
  margin: 8px 0 0;
  padding: 8px 10px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  max-height: 180px;
  overflow: auto;
}
</style>
