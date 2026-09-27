<template>
  <div v-loading="loading">
    <div class="page-header">
      <h2 class="page-title">时间点恢复</h2>
      <el-button :icon="Refresh" circle @click="loadAll(true)" />
    </div>

    <el-row :gutter="16" class="mb-3">
      <el-col :span="8" :xs="24">
        <el-card shadow="never">
          <div class="card-label">可恢复时间范围</div>
          <div v-if="options.recoverable" class="card-value range">
            {{ formatIso(options.earliest_recoverable_at) }}
            <span class="range-sep">~</span>
            {{ formatIso(options.latest_recoverable_at) }}
          </div>
          <div v-else class="card-value muted">{{ options.reason || '当前不可恢复' }}</div>
          <div class="card-sub">时区：{{ options.timezone || 'Asia/Shanghai' }}</div>
        </el-card>
      </el-col>
      <el-col :span="8" :xs="24">
        <el-card shadow="never">
          <div class="card-label">Binlog</div>
          <div class="card-value">
            <el-tag :type="options.binlog?.log_bin ? 'success' : 'danger'" size="small">
              {{ options.binlog?.log_bin ? '已开启' : '未开启' }}
            </el-tag>
          </div>
          <div class="card-sub">
            保留约 {{ options.binlog?.expire_days ?? '-' }} 天 ·
            {{ options.binlog?.file_count ?? 0 }} 个文件
          </div>
        </el-card>
      </el-col>
      <el-col :span="8" :xs="24">
        <el-card shadow="never">
          <div class="card-label">备份基线</div>
          <div class="card-value">全量 {{ options.full_backups?.length ?? 0 }} · 增量 {{ options.incremental_backups?.length ?? 0 }}</div>
          <div class="card-sub">数据来自对象存储索引</div>
        </el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab" class="mb-3">
      <el-tab-pane label="整实例恢复" name="instance">
        <el-alert type="warning" show-icon :closable="false" class="mb-3">
          <template #title>
            整实例恢复会<strong>停止 MySQL</strong>并将<strong>所有业务库</strong>回滚到指定时刻，操作不可逆，请在维护窗口执行。
          </template>
        </el-alert>

        <el-card shadow="never" class="mb-3">
          <template #header>执行恢复</template>
          <el-form label-width="120px" @submit.prevent>
            <el-form-item label="目标时间">
              <PitrTimePicker
                v-model="targetAt"
                :earliest-at="options.earliest_recoverable_at"
                :latest-at="options.latest_recoverable_at"
                :recoverable="options.recoverable"
                :disabled="!options.recoverable"
              />
              <el-button class="ml-2 preview-btn" :loading="previewing" :disabled="!targetAt" @click="preview">预览计划</el-button>
            </el-form-item>
            <el-form-item label="指定全量备份">
              <el-select v-model="fullBackupTs" clearable filterable placeholder="留空则自动选择最近备份" style="width: 320px">
                <el-option
                  v-for="item in options.full_backups || []"
                  :key="item.timestamp"
                  :label="`${item.display_time} (${item.timestamp})`"
                  :value="item.timestamp"
                />
              </el-select>
            </el-form-item>
            <el-alert v-if="previewResult" :type="previewResult.valid ? 'success' : 'error'" :closable="false" show-icon class="preview-box">
              <template v-if="previewResult.valid">
                将基于 {{ previewResult.plan?.base_backup_type === 'full' ? '全量' : '增量' }}
                备份 <code>{{ previewResult.plan?.base_display_time }}</code>
                恢复至 <code>{{ previewResult.target_time }}</code>
              </template>
              <template v-else>{{ previewResult.reason }}</template>
            </el-alert>
            <el-form-item>
              <el-button
                type="danger"
                :loading="triggering"
                :disabled="!options.recoverable || pitrRunning"
                @click="triggerRestore"
              >
                开始整实例恢复
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never" class="mb-3">
          <template #header>可恢复窗口（按备份基线）</template>
          <el-table :data="options.windows || []" stripe size="small" max-height="280">
            <el-table-column prop="backup_type" label="类型" width="80">
              <template #default="{ row }">{{ row.backup_type === 'full' ? '全量' : '增量' }}</template>
            </el-table-column>
            <el-table-column prop="display_time" label="备份时间" width="180" />
            <el-table-column label="可回滚至（起始）" min-width="180">
              <template #default="{ row }">{{ formatIso(row.window_start) }}</template>
            </el-table-column>
            <el-table-column label="可回滚至（最晚）" min-width="180">
              <template #default="{ row }">{{ formatIso(row.window_end) }}</template>
            </el-table-column>
          </el-table>
          <ul v-if="options.notes?.length" class="notes">
            <li v-for="(note, idx) in options.notes" :key="idx">{{ note }}</li>
          </ul>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <span>整实例恢复记录</span>
            <el-tag v-if="pitrRunning" size="small" type="warning" class="ml-1">进行中</el-tag>
          </template>
          <el-table :data="jobs" stripe size="small">
            <el-table-column label="提交时间" width="170">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="target_time" label="目标时间" width="170" />
            <el-table-column prop="status_display" label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status_display }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="耗时" width="80">
              <template #default="{ row }">{{ row.duration_seconds != null ? row.duration_seconds + 's' : '-' }}</template>
            </el-table-column>
            <el-table-column prop="error_message" label="错误" min-width="140" show-overflow-tooltip />
            <el-table-column type="expand" width="48">
              <template #default="{ row }">
                <pre class="job-log">{{ row.output_log || '无日志' }}</pre>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="单库恢复" name="database">
        <el-alert type="info" show-icon :closable="false" class="mb-3">
          <template #title>
            单库恢复在<strong>独立临时实例</strong>上完成 PITR，再导出并写入生产库，<strong>不停生产 MySQL</strong>。仅同步所选库的数据，跨库外键可能不一致；推荐先恢复到新库名验证。
          </template>
        </el-alert>

        <el-card shadow="never" class="mb-3">
          <template #header>执行单库恢复</template>
          <el-form label-width="120px" @submit.prevent>
            <el-form-item label="源数据库">
              <el-select v-model="dbSource" filterable placeholder="选择要恢复的库" style="width: 280px">
                <el-option v-for="db in databases" :key="db.name" :label="db.name" :value="db.name" />
              </el-select>
            </el-form-item>
            <el-form-item label="目标时间">
              <PitrTimePicker
                v-model="dbTargetAt"
                :earliest-at="options.earliest_recoverable_at"
                :latest-at="options.latest_recoverable_at"
                :recoverable="options.recoverable"
                :disabled="!options.recoverable"
              />
              <el-button class="ml-2 preview-btn" :loading="dbPreviewing" :disabled="!dbSource || !dbTargetAt" @click="previewDatabase">
                预览计划
              </el-button>
            </el-form-item>
            <el-form-item label="写回策略">
              <el-radio-group v-model="dbWriteMode">
                <el-radio value="new_database">恢复到新库（推荐）</el-radio>
                <el-radio value="overwrite">覆盖原库</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item v-if="dbWriteMode === 'new_database'" label="新库名">
              <el-input v-model="dbTargetName" placeholder="例如 demo_restored_20250601" style="width: 280px" />
            </el-form-item>
            <el-form-item label="指定全量备份">
              <el-select v-model="dbFullBackupTs" clearable filterable placeholder="留空则自动选择" style="width: 320px">
                <el-option
                  v-for="item in options.full_backups || []"
                  :key="item.timestamp"
                  :label="`${item.display_time} (${item.timestamp})`"
                  :value="item.timestamp"
                />
              </el-select>
            </el-form-item>
            <el-alert v-if="dbPreviewResult" :type="dbPreviewResult.valid ? 'success' : 'error'" :closable="false" show-icon class="preview-box">
              <template v-if="dbPreviewResult.valid">
                将恢复库 <code>{{ dbPreviewResult.source_database }}</code> 至
                <code>{{ dbPreviewResult.target_time }}</code>，写入生产库
                <code>{{ dbPreviewResult.target_database }}</code>
                （{{ dbPreviewResult.write_mode === 'overwrite' ? '覆盖' : '新库' }}）
                <ul v-if="dbPreviewResult.workflow?.length" class="workflow-list">
                  <li v-for="(step, idx) in dbPreviewResult.workflow" :key="idx">{{ step }}</li>
                </ul>
              </template>
              <template v-else>{{ dbPreviewResult.reason }}</template>
            </el-alert>
            <el-form-item>
              <el-button
                type="primary"
                :loading="dbTriggering"
                :disabled="!options.recoverable || dbPitrRunning || !dbSource || !dbTargetAt"
                @click="triggerDatabaseRestore"
              >
                开始单库恢复
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <span>单库恢复记录</span>
            <el-tag v-if="dbPitrRunning" size="small" type="warning" class="ml-1">进行中</el-tag>
          </template>
          <el-table :data="dbJobs" stripe size="small">
            <el-table-column label="提交时间" width="170">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="source_database" label="源库" width="120" />
            <el-table-column prop="target_database" label="目标库" width="120" />
            <el-table-column prop="target_time" label="目标时间" width="170" />
            <el-table-column prop="write_mode_display" label="策略" width="100" />
            <el-table-column prop="status_display" label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status_display }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="耗时" width="80">
              <template #default="{ row }">{{ row.duration_seconds != null ? row.duration_seconds + 's' : '-' }}</template>
            </el-table-column>
            <el-table-column prop="error_message" label="错误" min-width="120" show-overflow-tooltip />
            <el-table-column type="expand" width="48">
              <template #default="{ row }">
                <pre class="job-log">{{ row.output_log || '无日志' }}</pre>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { backupApi, databaseApi } from '@/api'
import { formatDateTime } from '@/utils/datetime'
import { defaultTargetTime } from '@/utils/pitrTime'
import PitrTimePicker from '@/components/PitrTimePicker.vue'

const loading = ref(false)
const activeTab = ref('instance')

const previewing = ref(false)
const triggering = ref(false)
const pitrRunning = ref(false)
const options = ref({ recoverable: false, full_backups: [], incremental_backups: [], windows: [] })
const jobs = ref([])
const targetAt = ref('')
const fullBackupTs = ref('')
const previewResult = ref(null)

const dbPreviewing = ref(false)
const dbTriggering = ref(false)
const dbPitrRunning = ref(false)
const dbJobs = ref([])
const databases = ref([])
const dbSource = ref('')
const dbTargetAt = ref('')
const dbWriteMode = ref('new_database')
const dbTargetName = ref('')
const dbFullBackupTs = ref('')
const dbPreviewResult = ref(null)

let pollTimer = null

function formatIso(value) {
  if (!value) return '-'
  return formatDateTime(value)
}

function statusType(status) {
  return { success: 'success', failed: 'danger', running: 'primary', pending: 'info' }[status] || 'info'
}

function initDefaultTargetTime() {
  if (!options.value.recoverable) return
  const latest = defaultTargetTime(options.value.earliest_recoverable_at, options.value.latest_recoverable_at)
  if (!targetAt.value) targetAt.value = latest
  if (!dbTargetAt.value) dbTargetAt.value = latest
}

async function loadOptions(refresh = false) {
  const { data } = await backupApi.pitrOptions(refresh)
  options.value = data
  initDefaultTargetTime()
}

async function loadDatabases() {
  const { data } = await databaseApi.list()
  databases.value = data.items || data || []
}

async function loadJobs() {
  const { data } = await backupApi.pitrJobs()
  jobs.value = data.items || []
  pitrRunning.value = data.running
  return data.running
}

async function loadDbJobs() {
  const { data } = await backupApi.dbPitrJobs()
  dbJobs.value = data.items || []
  dbPitrRunning.value = data.running
  return data.running
}

async function loadAll(refresh = false) {
  loading.value = true
  try {
    await Promise.all([loadOptions(refresh), loadJobs(), loadDbJobs(), loadDatabases()])
  } finally {
    loading.value = false
  }
}

async function preview() {
  if (!targetAt.value) return
  previewing.value = true
  try {
    const payload = { target_time: targetAt.value }
    if (fullBackupTs.value) payload.full_backup_timestamp = fullBackupTs.value
    const { data } = await backupApi.pitrPreview(payload)
    previewResult.value = data
  } finally {
    previewing.value = false
  }
}

async function triggerRestore() {
  if (!targetAt.value) return
  await preview()
  if (!previewResult.value?.valid) return

  await ElMessageBox.confirm(
    `将把整实例 MySQL 回滚到 ${targetAt.value}。\n\n此操作会停止数据库并覆盖当前所有数据，确定继续？`,
    '整实例恢复确认',
    { type: 'error', confirmButtonText: '确认恢复', cancelButtonText: '取消' }
  )

  triggering.value = true
  try {
    const payload = { target_time: targetAt.value }
    if (fullBackupTs.value) payload.full_backup_timestamp = fullBackupTs.value
    await backupApi.pitrTrigger(payload)
    ElMessage.success('整实例恢复任务已提交，MySQL 将短暂不可用')
    await loadJobs()
    startPoll()
  } finally {
    triggering.value = false
  }
}

function buildDbPayload() {
  const payload = {
    source_database: dbSource.value,
    target_time: dbTargetAt.value,
    write_mode: dbWriteMode.value,
  }
  if (dbWriteMode.value === 'new_database') {
    payload.target_database = dbTargetName.value.trim()
  }
  if (dbFullBackupTs.value) payload.full_backup_timestamp = dbFullBackupTs.value
  return payload
}

async function previewDatabase() {
  if (!dbSource.value || !dbTargetAt.value) return
  if (dbWriteMode.value === 'new_database' && !dbTargetName.value.trim()) {
    ElMessage.warning('请填写新库名')
    return
  }
  dbPreviewing.value = true
  try {
    const { data } = await backupApi.dbPitrPreview(buildDbPayload())
    dbPreviewResult.value = data
  } finally {
    dbPreviewing.value = false
  }
}

async function triggerDatabaseRestore() {
  if (!dbSource.value || !dbTargetAt.value) return
  await previewDatabase()
  if (!dbPreviewResult.value?.valid) return

  const targetDb = dbPreviewResult.value.target_database
  const confirmMsg =
    dbWriteMode.value === 'overwrite'
      ? `将把生产库「${dbSource.value}」覆盖为 ${dbTargetAt.value} 时刻的数据。\n\n此操作不可逆，确定继续？`
      : `将把库「${dbSource.value}」在 ${dbTargetAt.value} 时刻的数据导入到新库「${targetDb}」。\n\n确定继续？`

  await ElMessageBox.confirm(confirmMsg, '单库恢复确认', {
    type: dbWriteMode.value === 'overwrite' ? 'error' : 'warning',
    confirmButtonText: '确认恢复',
    cancelButtonText: '取消',
  })

  dbTriggering.value = true
  try {
    await backupApi.dbPitrTrigger(buildDbPayload())
    ElMessage.success('单库恢复任务已提交，生产 MySQL 保持在线')
    await loadDbJobs()
    startPoll()
  } finally {
    dbTriggering.value = false
  }
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(async () => {
    const [instanceRunning, dbRunning] = await Promise.all([loadJobs(), loadDbJobs()])
    if (!instanceRunning && !dbRunning) {
      stopPoll()
      await loadOptions(true)
      await loadDatabases()
    }
  }, 5000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(async () => {
  await loadAll()
  if (pitrRunning.value || dbPitrRunning.value) startPoll()
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
.mb-3 { margin-bottom: 16px; }
.ml-1 { margin-left: 6px; }
.ml-2 { margin-left: 8px; }
.card-label { color: #909399; font-size: 13px; margin-bottom: 8px; }
.card-value { font-size: 16px; font-weight: 600; color: #303133; }
.card-value.range { font-size: 14px; font-weight: 500; line-height: 1.6; }
.range-sep { margin: 0 6px; color: #909399; }
.card-sub { color: #909399; font-size: 12px; margin-top: 8px; }
.muted { color: #909399; font-weight: 400; }
.preview-box { margin: 0 0 16px 120px; max-width: 640px; }
.preview-btn { vertical-align: top; margin-top: 4px; }
.notes { margin: 12px 0 0; padding-left: 18px; color: #909399; font-size: 13px; }
.workflow-list { margin: 8px 0 0; padding-left: 18px; font-size: 13px; }
.job-log {
  margin: 0;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  background: #f5f7fa;
  border-radius: 4px;
  max-height: 320px;
  overflow: auto;
}
</style>
