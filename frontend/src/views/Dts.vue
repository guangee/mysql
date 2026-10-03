<template>
  <div v-loading="loading">
    <div class="toolbar">
      <div>
        <h2 class="page-title">数据同步</h2>
        <div class="page-hint">全量会先同步全部表结构，再逐表导入数据，之后按 binlog 做增量。全量会覆盖目标库里的同名表。</div>
      </div>
      <div>
        <el-button type="primary" @click="openCreate">新建同步任务</el-button>
        <el-button :icon="Refresh" circle @click="load(false)" />
      </div>
    </div>

    <el-table :data="items" stripe row-key="id">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="detail">
            <div>目标账号：{{ row.target_user }}</div>
            <div v-if="row.error_message" class="error-line">{{ row.error_message }}</div>
            <pre class="log">{{ row.log_text || '暂无日志' }}</pre>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column label="目标" min-width="180">
        <template #default="{ row }">{{ row.target_host }}:{{ row.target_port }}</template>
      </el-table-column>
      <el-table-column label="本地库 → 目标库" min-width="220">
        <template #default="{ row }">
          <span v-if="row.source_database">{{ row.source_database }} → {{ row.target_database }}</span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="同步进度" min-width="260">
        <template #default="{ row }">
          <div class="progress-cell">
            <div class="progress-meta">{{ scaleText(row) }}</div>
            <el-progress
              :percentage="row.progress_percent || 0"
              :status="progressStatus(row)"
              :stroke-width="8"
            />
            <div class="progress-detail">{{ progressText(row) }}</div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="延迟" width="90">
        <template #default="{ row }">
          <span v-if="row.lag_seconds == null">-</span>
          <span v-else>{{ row.lag_seconds }} 秒</span>
        </template>
      </el-table-column>
      <el-table-column label="位点" min-width="180">
        <template #default="{ row }">{{ row.position || '-' }}</template>
      </el-table-column>
      <el-table-column prop="events_applied" label="增量事件" width="100" />
      <el-table-column label="操作" width="290" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          <el-button link type="primary" :disabled="running(row)" @click="start(row, false)">
            {{ row.binlog_file ? '继续' : '启动' }}
          </el-button>
          <el-button link type="warning" :disabled="!running(row)" @click="act(row, 'pause')">暂停</el-button>
          <el-button link :disabled="row.status === 'stopped'" @click="act(row, 'stop')">停止</el-button>
          <el-button link type="danger" :disabled="running(row)" @click="restartFull(row)">重新全量</el-button>
          <el-button link type="danger" :disabled="running(row)" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="visible" title="新建同步任务" width="560px">
      <el-form label-width="96px">
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="目标地址">
          <el-input v-model="form.target_host" placeholder="远程 MySQL 主机" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="form.target_port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="账号">
          <el-input v-model="form.target_user" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="本地库">
          <el-select v-model="form.source_database" filterable style="width: 100%" placeholder="选择当前实例的库">
            <el-option v-for="name in databases" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标库">
          <el-select
            v-model="form.target_database"
            filterable
            style="width: 100%"
            :placeholder="targetDatabases.length ? '选择目标实例上的库' : '先测试连接，再选择目标库'"
            :disabled="!targetDatabases.length"
          >
            <el-option v-for="name in targetDatabases" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="testing" @click="testConn">测试连接</el-button>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { dtsApi } from '@/api'

const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const visible = ref(false)
const items = ref([])
const databases = ref([])
const targetDatabases = ref([])
const form = reactive({
  name: '',
  target_host: '',
  target_port: 3306,
  target_user: '',
  password: '',
  source_database: '',
  target_database: '',
})
let timer = null

function formatBytes(size) {
  let value = Number(size || 0)
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  for (let i = 0; i < units.length; i += 1) {
    if (value < 1024 || i === units.length - 1) {
      if (units[i] === 'B') return `${Math.round(value)} B`
      const text = value >= 100 ? value.toFixed(0) : value.toFixed(1)
      return `${text.replace(/\.0$/, '')} ${units[i]}`
    }
    value /= 1024
  }
  return '0 B'
}

function formatRows(count) {
  const value = Number(count || 0)
  if (value >= 100000000) return `约 ${(value / 100000000).toFixed(1)} 亿行`
  if (value >= 10000) return `约 ${(value / 10000).toFixed(1)} 万行`
  return `约 ${value} 行`
}

function scaleText(row) {
  return `${row.table_total || 0} 张表 · ${formatRows(row.rows_total)} · ${formatBytes(row.bytes_total)}`
}

function progressStatus(row) {
  if (row.status === 'error') return 'exception'
  if (row.status === 'incremental' || row.progress_percent >= 100) return 'success'
  if (row.status === 'paused') return 'warning'
  return ''
}

function progressText(row) {
  if (row.status === 'incremental') return '全量完成，增量同步中'
  if (row.status === 'full_sync' && row.full_phase === 'export') return '正在导出一致性快照'
  if (row.status === 'full_sync' && row.full_phase === 'structure') {
    return `同步表结构 ${row.structure_done || 0}/${row.table_total || 0}`
  }
  if (row.status === 'full_sync') {
    const current = row.current_table ? `，正在同步 ${row.current_table}` : ''
    return `逐表全量 ${row.table_done || 0}/${row.table_total || 0} 表 · ${formatBytes(row.bytes_done)}/${formatBytes(row.bytes_total)}${current}`
  }
  if ((row.progress_percent || 0) > 0) {
    return `${row.table_done || 0}/${row.table_total || 0} 表 · ${formatBytes(row.bytes_done)}/${formatBytes(row.bytes_total)}`
  }
  return '尚未开始'
}

function openDetail(row) {
  router.push(`/dts/${row.id}`)
}

function statusType(status) {
  return {
    incremental: 'success',
    full_sync: 'primary',
    paused: 'warning',
    error: 'danger',
    stopped: 'info',
  }[status] || 'info'
}

function running(row) {
  return row.status === 'full_sync' || row.status === 'incremental'
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const { data } = await dtsApi.tasks()
    items.value = data.items || []
  } finally {
    loading.value = false
  }
}

async function openCreate() {
  const { data } = await dtsApi.databases()
  databases.value = data.items || []
  form.name = ''
  form.target_host = ''
  form.target_port = 3306
  form.target_user = ''
  form.password = ''
  form.source_database = ''
  form.target_database = ''
  targetDatabases.value = []
  visible.value = true
}

async function testConn() {
  testing.value = true
  try {
    const { data } = await dtsApi.testConnection({
      target_host: form.target_host,
      target_port: form.target_port,
      target_user: form.target_user,
      password: form.password,
    })
    targetDatabases.value = data.databases || []
    if (!targetDatabases.value.includes(form.target_database)) {
      form.target_database = ''
    }
    ElMessage.success(
      targetDatabases.value.length
        ? `连接成功，目标实例有 ${targetDatabases.value.length} 个可选库`
        : '连接成功，但目标实例上没有可选的业务库',
    )
  } finally {
    testing.value = false
  }
}

async function create() {
  if (!form.source_database || !form.target_database) {
    ElMessage.warning('请选择本地库和目标库')
    return
  }
  saving.value = true
  try {
    await dtsApi.createTask({ ...form })
    ElMessage.success('任务已创建')
    visible.value = false
    await load(true)
  } finally {
    saving.value = false
  }
}

async function start(row, full) {
  if (!row.binlog_file || full) {
    try {
      await ElMessageBox.confirm(
        full
          ? `重新全量会把本地库 ${row.source_database} 覆盖写入 ${row.target_host}:${row.target_port} 的 ${row.target_database}，其中同名表会被替换。确认继续？`
          : `首次启动会把本地库 ${row.source_database} 全量同步到 ${row.target_host}:${row.target_port} 的 ${row.target_database}，并覆盖其中的同名表，完成后自动进入增量。确认启动？`,
        '确认同步',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  await dtsApi.action(row.id, 'start', { full })
  ElMessage.success(full || !row.binlog_file ? '已开始全量同步' : '已继续增量同步')
  await load(true)
}

async function restartFull(row) {
  await start(row, true)
}

async function act(row, action) {
  await dtsApi.action(row.id, action)
  await load(true)
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除同步任务「${row.name}」？已同步到目标的数据不会回滚。`, '删除任务', { type: 'warning' })
  } catch {
    return
  }
  await dtsApi.removeTask(row.id)
  await load(true)
}

onMounted(() => {
  load(false)
  timer = setInterval(() => load(true), 3000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }
.page-title { margin: 0 0 4px; }
.page-hint { color: #909399; font-size: 13px; }
.progress-cell { min-width: 220px; }
.progress-meta, .progress-detail { color: #606266; font-size: 12px; line-height: 1.4; }
.progress-detail { color: #909399; }
:deep(.el-progress-bar__inner) { transition: none; }
:deep(.el-progress__text) { font-size: 12px !important; }
.detail { padding: 4px 12px 8px 48px; color: #606266; font-size: 13px; }
.error-line { color: #f56c6c; margin: 6px 0; white-space: pre-wrap; }
.log {
  margin: 8px 0 0;
  padding: 8px 10px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  max-height: 240px;
  overflow: auto;
}
</style>
