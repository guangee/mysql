<template>
  <div v-loading="loading">
    <div class="page-header">
      <div>
        <h2 v-if="!embedded" class="page-title">备份文件</h2>
        <div v-if="catalog.last_synced_at" class="catalog-meta">
          索引同步于 {{ formatDateTime(catalog.last_synced_at) }}
          <el-tag v-if="catalog.syncing" size="small" type="warning" class="ml-1">同步中</el-tag>
        </div>
      </div>
      <div class="header-actions">
        <el-button @click="openRetentionDialog">备份策略</el-button>
        <el-button type="primary" @click="openUploadDialog">上传备份</el-button>
        <el-button type="warning" @click="openCleanupDialog">手动清理</el-button>
        <el-button :icon="Refresh" circle @click="refreshAll" />
      </div>
    </div>

    <el-alert v-if="cleanupRunning" type="info" title="清理任务进行中，完成后将自动刷新列表" show-icon class="mb-3" />

    <el-row :gutter="16" class="stats-row">
      <el-col :span="12" :xs="24">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">全量备份占用</div>
          <div class="stat-value">{{ fullTotalSize }}</div>
          <div class="stat-sub">{{ fullItems.length }} 个文件</div>
        </el-card>
      </el-col>
      <el-col :span="12" :xs="24">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">增量备份占用</div>
          <div class="stat-value">{{ incrementalTotalSize }}</div>
          <div class="stat-sub">{{ incrementalItems.length }} 个文件</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="全量备份" name="full">
          <div class="tab-summary">
            共 {{ fullItems.length }} 个文件，保留 {{ retention.full_retention_days }} 天
          </div>
          <BackupFileTable
            :items="fullItems"
            empty-text="暂无全量备份"
            show-restore
            @download="download"
            @restore="restoreToBackup"
          />
        </el-tab-pane>
        <el-tab-pane label="增量备份" name="incremental">
          <div class="tab-summary">
            共 {{ incrementalItems.length }} 个文件，保留 {{ retention.incremental_retention_days }} 天
          </div>
          <BackupFileTable
            :items="incrementalItems"
            empty-text="暂无增量备份"
            @download="download"
          />
        </el-tab-pane>
      </el-tabs>
      <div class="legend">
        <span class="legend-item"><i class="dot ok" />正常</span>
        <span class="legend-item"><i class="dot warning" />临近过期（剩余不足保留期 1/3）</span>
        <span class="legend-item"><i class="dot expired" />已过期</span>
      </div>
    </el-card>

    <!-- 备份策略 -->
    <el-dialog v-model="retentionVisible" title="备份策略" width="680px" destroy-on-close>
      <el-form label-width="120px" @submit.prevent="saveRetention">
        <el-divider content-position="left">备份周期</el-divider>
        <el-form-item label="全量备份">
          <div class="schedule-field">
            <el-switch v-model="retentionForm.full_backup_enabled" />
            <CronSchedulePicker
              v-model="retentionForm.full_backup_schedule"
              :disabled="!retentionForm.full_backup_enabled"
              :allowed-modes="['daily', 'weekly', 'monthly', 'custom']"
            />
          </div>
        </el-form-item>
        <el-form-item label="增量备份">
          <div class="schedule-field">
            <el-switch v-model="retentionForm.incremental_backup_enabled" />
            <CronSchedulePicker
              v-model="retentionForm.incremental_backup_schedule"
              :disabled="!retentionForm.incremental_backup_enabled"
              :allowed-modes="['every_minutes', 'hourly', 'every_hours', 'daily', 'custom']"
            />
          </div>
        </el-form-item>

        <el-divider content-position="left">保留天数</el-divider>
        <el-form-item label="全量保留">
          <el-input-number v-model="retentionForm.full_retention_days" :min="1" :max="3650" />
          <span class="unit">天</span>
        </el-form-item>
        <el-form-item label="增量保留">
          <el-input-number v-model="retentionForm.incremental_retention_days" :min="1" :max="3650" />
          <span class="unit">天</span>
        </el-form-item>

        <el-divider content-position="left">定时删除</el-divider>
        <el-form-item label="本地清理">
          <div class="schedule-field">
            <el-switch v-model="retentionForm.cleanup_local_enabled" />
            <CronSchedulePicker
              v-model="retentionForm.cleanup_local_schedule"
              :disabled="!retentionForm.cleanup_local_enabled"
              :allowed-modes="['every_minutes', 'hourly', 'daily', 'custom']"
            />
          </div>
        </el-form-item>
        <el-form-item label="对象存储清理">
          <div class="schedule-field">
            <el-switch v-model="retentionForm.cleanup_s3_enabled" />
            <CronSchedulePicker
              v-model="retentionForm.cleanup_s3_schedule"
              :disabled="!retentionForm.cleanup_s3_enabled"
              :allowed-modes="['hourly', 'daily', 'custom']"
            />
          </div>
        </el-form-item>
        <div class="hint">
          选择执行频率与时间后，系统会自动生成 Cron 并同步到 MySQL 容器定时任务。
        </div>
      </el-form>
      <template #footer>
        <el-button @click="retentionVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRetention">保存</el-button>
      </template>
    </el-dialog>

    <!-- 上传备份 -->
    <el-dialog v-model="uploadVisible" title="上传备份文件" width="520px" destroy-on-close @open="loadStorages">
      <el-alert type="info" :closable="false" show-icon class="mb-3">
        文件名须为 <code>YYYYMMDD_HHMMSS_full|incr_v版本.tar.gz</code>（兼容旧名 <code>backup_YYYYMMDD_HHMMSS.tar.gz</code>），上传至所选对象存储后自动刷新列表。
      </el-alert>
      <el-form label-width="100px">
        <el-form-item label="存储源">
          <el-select v-model="uploadForm.storageId" placeholder="选择存储" style="width: 100%">
            <el-option v-for="s in storages" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备份类型">
          <el-radio-group v-model="uploadForm.backupType">
            <el-radio value="full">全量</el-radio>
            <el-radio value="incremental">增量</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="文件">
          <el-upload
            ref="uploadRef"
            drag
            :auto-upload="false"
            :limit="1"
            accept=".tar.gz"
            :on-change="onUploadFileChange"
            :on-remove="onUploadFileRemove"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">拖拽或点击选择 <em>*_full|incr_v*.tar.gz</em></div>
          </el-upload>
        </el-form-item>
        <el-progress v-if="uploading" :percentage="uploadProgress" :stroke-width="8" />
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false" :disabled="uploading">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadForm.file || !uploadForm.storageId" @click="submitUpload">
          开始上传
        </el-button>
      </template>
    </el-dialog>

    <!-- 手动清理 -->
    <el-dialog v-model="cleanupVisible" title="手动清理过期备份" width="780px" destroy-on-close @open="loadCleanupJobs">
      <el-alert type="info" :closable="false" show-icon class="cleanup-tip">
        <template #title>
          「对象存储」与「全部清理」会删除列表中红色「已过期」的对象存储备份（与页面状态完全一致）。
          「本地」仅清理带 <code>.delete_after</code> 标记的目录。
        </template>
      </el-alert>
      <div v-if="expiredTotal > 0" class="expired-hint">
        当前列表已过期：全量 {{ expiredFullCount }} 个，增量 {{ expiredIncrementalCount }} 个
      </div>
      <el-space wrap class="cleanup-actions">
        <el-button type="danger" :loading="cleaning" :disabled="cleanupRunning || !expiredTotal" @click="triggerCleanup('s3')">
          清理对象存储过期{{ expiredTotal ? ` (${expiredTotal})` : '' }}
        </el-button>
        <el-button type="warning" :loading="cleaning" :disabled="cleanupRunning" @click="triggerCleanup('all')">
          全部清理
        </el-button>
        <el-button type="warning" plain :loading="cleaning" :disabled="cleanupRunning" @click="triggerCleanup('local')">
          仅清理本地
        </el-button>
        <el-button :disabled="cleaning" @click="loadCleanupJobs">刷新记录</el-button>
      </el-space>
      <el-table :data="cleanupJobs" stripe class="cleanup-table" size="small" max-height="360">
        <el-table-column label="时间" width="170" class-name="time-col">
          <template #default="{ row }">
            <span class="time-cell">{{ formatDateTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="scope_display" label="范围" width="100" />
        <el-table-column prop="trigger_display" label="触发" width="80" />
        <el-table-column prop="status_display" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status_display }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="duration_seconds" label="耗时" width="80">
          <template #default="{ row }">{{ row.duration_seconds != null ? row.duration_seconds + 's' : '-' }}</template>
        </el-table-column>
        <el-table-column label="结果摘要" min-width="160">
          <template #default="{ row }">
            <span>{{ cleanupSummary(row.output_log) }}</span>
          </template>
        </el-table-column>
        <el-table-column type="expand" width="48">
          <template #default="{ row }">
            <pre class="cleanup-log">{{ row.output_log || '无日志' }}</pre>
          </template>
        </el-table-column>
        <el-table-column prop="error_message" label="错误" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-dialog>

    <!-- 下载方式 -->
    <el-dialog v-model="downloadVisible" title="下载备份" width="520px" destroy-on-close>
      <div v-if="downloadTarget.filename" class="download-meta">
        <div><strong>{{ downloadTarget.filename }}</strong></div>
        <div class="download-sub">存储源：{{ downloadTarget.storageName }}</div>
      </div>
      <el-alert
        v-if="downloadOptions.direct_hint"
        :type="downloadOptions.direct_accessible ? 'info' : 'warning'"
        :closable="false"
        show-icon
        class="download-tip"
      >
        {{ downloadOptions.direct_hint }}
      </el-alert>
      <div class="download-actions">
        <el-button type="primary" :loading="downloading === 'proxy'" @click="downloadViaProxy">
          API 代理下载
        </el-button>
        <el-button :loading="downloading === 'direct'" @click="downloadViaDirect">
          直连对象存储
        </el-button>
      </div>
      <p class="download-note">代理下载经控制台转发，适用于对象存储仅内网可达；直连使用预签名链接，大文件不占用控制台带宽。</p>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Refresh, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { backupApi, storageApi } from '@/api'
import { formatDateTime } from '@/utils/datetime'
import { formatBackupTotalSize, sumBackupItemsBytes } from '@/utils/format'
import BackupFileTable from '@/components/BackupFileTable.vue'
import CronSchedulePicker from '@/components/CronSchedulePicker.vue'

defineProps({
  embedded: { type: Boolean, default: false },
})

const loading = ref(false)
const saving = ref(false)
const cleaning = ref(false)
const cleanupRunning = ref(false)
const activeTab = ref('full')
const retentionVisible = ref(false)
const cleanupVisible = ref(false)
const uploadVisible = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const storages = ref([])
const uploadRef = ref(null)
const uploadForm = ref({
  storageId: null,
  backupType: 'full',
  file: null,
})
const fullItems = ref([])
const incrementalItems = ref([])
const cleanupJobs = ref([])
let pollTimer = null

const retention = ref({
  full_backup_schedule: '0 2 * * 0',
  incremental_backup_schedule: '0 3 * * *',
  full_backup_enabled: true,
  incremental_backup_enabled: true,
  full_retention_days: 30,
  incremental_retention_days: 14,
  cleanup_local_schedule: '0 * * * *',
  cleanup_s3_schedule: '0 4 * * *',
  cleanup_local_enabled: true,
  cleanup_s3_enabled: true,
  updated_at: null,
})
const catalog = ref({
  last_synced_at: null,
  sync_status: 'idle',
  file_count: 0,
  syncing: false,
})
const downloadVisible = ref(false)
const downloading = ref('')
const downloadTarget = ref({ storageId: null, key: '', filename: '', storageName: '' })
const downloadOptions = ref({
  direct_url: '',
  direct_accessible: false,
  direct_hint: '',
})

const retentionForm = ref({})

const fullTotalSize = computed(() => formatBackupTotalSize(sumBackupItemsBytes(fullItems.value)))
const incrementalTotalSize = computed(() => formatBackupTotalSize(sumBackupItemsBytes(incrementalItems.value)))
const expiredFullCount = computed(() => fullItems.value.filter((x) => x.expiry_status === 'expired').length)
const expiredIncrementalCount = computed(() => incrementalItems.value.filter((x) => x.expiry_status === 'expired').length)
const expiredTotal = computed(() => expiredFullCount.value + expiredIncrementalCount.value)

function statusType(status) {
  return { success: 'success', failed: 'danger', running: 'primary', pending: 'info' }[status] || 'info'
}

function cleanupSummary(log) {
  if (!log) return '-'
  const indexed = log.match(/成功删除 (\d+) 个/)
  if (indexed) return `已删除 ${indexed[1]} 个`
  const legacy = log.match(/删除 (\d+) 个过期备份/)
  if (legacy) return `已删除 ${legacy[1]} 个`
  if (log.includes('列表中已过期 0 个')) return '无过期项'
  if (log.includes('没有需要清理的过期本地备份')) return '本地无过期项'
  if (log.includes('成功删除 0 个')) return '未删除任何文件'
  return '见展开日志'
}

function openRetentionDialog() {
  retentionForm.value = { ...retention.value }
  retentionVisible.value = true
}

function openCleanupDialog() {
  cleanupVisible.value = true
}

function openUploadDialog() {
  uploadForm.value = { storageId: null, backupType: 'full', file: null }
  uploadProgress.value = 0
  uploadVisible.value = true
}

async function loadStorages() {
  try {
    const { data } = await storageApi.list()
    storages.value = (data.results || data || []).filter((s) => s.enabled !== false)
    if (!uploadForm.value.storageId && storages.value.length) {
      uploadForm.value.storageId = storages.value[0].id
    }
  } catch {
    storages.value = []
  }
}

function onUploadFileChange(file) {
  uploadForm.value.file = file.raw
}

function onUploadFileRemove() {
  uploadForm.value.file = null
}

async function submitUpload() {
  if (!uploadForm.value.file || !uploadForm.value.storageId) return
  uploading.value = true
  uploadProgress.value = 0
  try {
    const formData = new FormData()
    formData.append('file', uploadForm.value.file)
    formData.append('storage_id', uploadForm.value.storageId)
    formData.append('backup_type', uploadForm.value.backupType)
    await backupApi.upload(formData, (e) => {
      if (e.total) uploadProgress.value = Math.round((e.loaded / e.total) * 100)
    })
    ElMessage.success('备份已上传，正在刷新列表')
    uploadVisible.value = false
    await loadFiles(true)
  } finally {
    uploading.value = false
  }
}

async function restoreToBackup(row) {
  if (!row.timestamp) {
    ElMessage.warning('无法识别备份时间戳')
    return
  }
  await ElMessageBox.confirm(
    `将把整实例 MySQL 恢复到全量备份 ${row.filename} 对应的状态。\n\n此操作会停止数据库并覆盖当前所有数据，确定继续？`,
    '全量恢复确认',
    { type: 'error', confirmButtonText: '确认恢复', cancelButtonText: '取消' }
  )
  await backupApi.restoreTrigger({ full_backup_timestamp: row.timestamp })
  ElMessage.success('全量恢复任务已提交，MySQL 将短暂不可用')
}

async function download(item) {
  downloadTarget.value = {
    storageId: item.storageId,
    key: item.key,
    filename: item.filename,
    storageName: item.storageName,
  }
  downloading.value = ''
  try {
    const { data } = await backupApi.download(item.storageId, item.key)
    downloadOptions.value = data
    downloadVisible.value = true
  } catch {
    /* error handled by interceptor */
  }
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function downloadViaProxy() {
  const { storageId, key, filename } = downloadTarget.value
  downloading.value = 'proxy'
  try {
    const { data } = await backupApi.downloadProxy(storageId, key)
    saveBlob(data, filename)
    ElMessage.success('已开始下载（API 代理）')
    downloadVisible.value = false
  } finally {
    downloading.value = ''
  }
}

async function downloadViaDirect() {
  const url = downloadOptions.value.direct_url
  if (!url) {
    ElMessage.error('无法获取直连地址')
    return
  }
  downloading.value = 'direct'
  try {
    window.open(url, '_blank')
    downloadVisible.value = false
  } finally {
    downloading.value = ''
  }
}

async function loadFiles(refresh = false) {
  loading.value = true
  try {
    const { data } = await backupApi.files(refresh)
    fullItems.value = data.full || []
    incrementalItems.value = data.incremental || []
    if (data.retention) {
      retention.value = { ...retention.value, ...data.retention }
    }
    if (data.catalog) {
      catalog.value = data.catalog
    }
  } finally {
    loading.value = false
  }
}

async function loadCleanupJobs() {
  const { data } = await backupApi.cleanupJobs()
  cleanupJobs.value = data.items || []
  cleanupRunning.value = data.running
  return data.running
}

async function refreshAll() {
  await loadFiles(true)
  await loadCleanupJobs()
}

async function saveRetention() {
  saving.value = true
  try {
    const { data } = await backupApi.updateRetention({
      full_backup_schedule: retentionForm.value.full_backup_schedule,
      incremental_backup_schedule: retentionForm.value.incremental_backup_schedule,
      full_backup_enabled: retentionForm.value.full_backup_enabled,
      incremental_backup_enabled: retentionForm.value.incremental_backup_enabled,
      full_retention_days: retentionForm.value.full_retention_days,
      incremental_retention_days: retentionForm.value.incremental_retention_days,
      cleanup_local_schedule: retentionForm.value.cleanup_local_schedule,
      cleanup_s3_schedule: retentionForm.value.cleanup_s3_schedule,
      cleanup_local_enabled: retentionForm.value.cleanup_local_enabled,
      cleanup_s3_enabled: retentionForm.value.cleanup_s3_enabled,
    })
    retention.value = data
    if (data.crontab_applied === false) {
      ElMessage.warning(data.crontab_message || '策略已保存，但同步 crontab 失败')
    } else {
      ElMessage.success('备份策略已保存并同步到定时任务')
    }
    retentionVisible.value = false
    await loadFiles()
  } finally {
    saving.value = false
  }
}

async function triggerCleanup(scope) {
  const labels = { local: '本地过期备份', s3: '对象存储过期备份', all: '本地与对象存储' }
  const hints = {
    local: '仅清理本地带 .delete_after 标记的目录，不会删除对象存储上的文件。',
    s3: `将删除列表中 ${expiredTotal.value} 个红色「已过期」的对象存储备份。`,
    all: `本地按 .delete_after 标记清理；对象存储删除列表中 ${expiredTotal.value} 个已过期备份。`,
  }
  await ElMessageBox.confirm(`${hints[scope]}\n\n确定立即清理${labels[scope]}？`, '手动清理', { type: 'warning' })
  cleaning.value = true
  try {
    await backupApi.triggerCleanup(scope)
    ElMessage.success('清理任务已提交，完成后请查看记录中的「结果摘要」与展开日志')
    await loadCleanupJobs()
    startPoll()
  } finally {
    cleaning.value = false
  }
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(async () => {
    const running = await loadCleanupJobs()
    if (!running) {
      stopPoll()
      await loadFiles(true)
    }
  }, 3000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(async () => {
  await loadFiles()
  await loadCleanupJobs()
  if (cleanupRunning.value) startPoll()
})

onUnmounted(stopPoll)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-title { margin: 0; }
.catalog-meta { margin-top: 4px; font-size: 12px; color: #909399; }
.ml-1 { margin-left: 6px; }
.header-actions { display: flex; gap: 8px; align-items: center; }
.mb-3 { margin-bottom: 16px; }
.stats-row { margin-bottom: 16px; }
.stat-card { text-align: center; }
.stat-label { color: #909399; font-size: 13px; margin-bottom: 8px; }
.stat-value { font-size: 28px; font-weight: 600; color: #303133; }
.stat-sub { color: #909399; font-size: 12px; margin-top: 6px; }
.tab-summary { margin-bottom: 12px; font-size: 13px; color: #909399; }
.unit { margin-left: 6px; color: #606266; }
.schedule-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.hint { font-size: 13px; color: #909399; margin: 0 0 8px 120px; }
.cleanup-actions { margin-bottom: 16px; }
.expired-hint {
  margin-bottom: 12px;
  padding: 8px 12px;
  font-size: 13px;
  color: #f56c6c;
  background: #fef0f0;
  border-radius: 4px;
}
.cleanup-tip { margin-bottom: 16px; }
.cleanup-table { margin-top: 8px; }
.cleanup-log {
  margin: 0;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  background: #f5f7fa;
  border-radius: 4px;
  max-height: 240px;
  overflow: auto;
}
.time-cell { white-space: nowrap; }
:deep(.time-col .cell) { white-space: nowrap; }
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
  font-size: 13px;
  color: #606266;
}
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.dot.ok { background: #67c23a; }
.dot.warning { background: #e6a23c; }
.dot.expired { background: #f56c6c; }
.download-meta { margin-bottom: 12px; }
.download-sub { margin-top: 4px; font-size: 13px; color: #909399; }
.download-tip { margin-bottom: 16px; }
.download-actions { display: flex; gap: 12px; flex-wrap: wrap; }
.download-note { margin: 16px 0 0; font-size: 12px; color: #909399; line-height: 1.6; }
</style>
