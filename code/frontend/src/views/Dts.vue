<template>
  <div v-loading="loading">
    <div class="toolbar">
      <div>
        <h2 class="page-title">数据同步</h2>
        <div class="page-hint">先配置远程 MySQL 连接并查看/新建远程库，再配置「本地库 → 远程库」的同步策略。当前只支持从本机向外备份。</div>
      </div>
      <el-button :icon="Refresh" circle @click="reloadCurrent" />
    </div>

    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="远程连接" name="connections">
        <div class="pane-actions">
          <el-button type="primary" @click="openConnectionDialog()">新建远程连接</el-button>
        </div>
        <el-table :data="connections" stripe row-key="id" @expand-change="onExpandConnection">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="db-panel">
                <div class="db-panel-head">
                  <span>远程库列表</span>
                  <div>
                    <el-button size="small" @click="loadRemoteDatabases(row)">刷新</el-button>
                    <el-button size="small" type="primary" @click="openRemoteDbDialog(row)">新建数据库</el-button>
                  </div>
                </div>
                <el-table :data="remoteDbMap[row.id] || []" size="small" stripe empty-text="暂无业务库，或尚未刷新">
                  <el-table-column prop="name" label="库名" min-width="160" />
                  <el-table-column prop="charset" label="字符集" width="120" />
                  <el-table-column prop="table_count" label="表数" width="80" />
                  <el-table-column label="大小" width="120">
                    <template #default="{ row: db }">{{ formatBytes(db.size_bytes) }}</template>
                  </el-table-column>
                </el-table>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column label="地址" min-width="180">
            <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
          </el-table-column>
          <el-table-column prop="user" label="账号" width="120" />
          <el-table-column prop="mysql_version" label="版本" min-width="140">
            <template #default="{ row }">{{ row.mysql_version || '-' }}</template>
          </el-table-column>
          <el-table-column label="策略数" width="80">
            <template #default="{ row }">{{ row.task_count || 0 }}</template>
          </el-table-column>
          <el-table-column label="最近连通" width="170">
            <template #default="{ row }">{{ formatDateTime(row.last_ok_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="240" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="testSaved(row)">测试</el-button>
              <el-button link type="primary" @click="openConnectionDialog(row)">编辑</el-button>
              <el-button link type="danger" :disabled="row.task_count > 0" @click="removeConnection(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="同步策略" name="policies">
        <div class="pane-actions">
          <el-button type="primary" @click="openCreate">新建同步策略</el-button>
        </div>
        <el-alert type="info" :closable="false" show-icon class="mb-3">
          方向固定为 <strong>本地库 → 远程连接上的库</strong>。全量会覆盖远程同名表，完成后按 binlog 增量。
        </el-alert>
        <el-table :data="items" stripe row-key="id">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="detail">
                <div>远程连接：{{ row.connection?.name || '-' }}（{{ row.target_user }}）</div>
                <div v-if="row.error_message" class="error-line">{{ row.error_message }}</div>
                <pre class="log">{{ row.log_text || '暂无日志' }}</pre>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="名称" min-width="120" />
          <el-table-column label="远程连接" min-width="160">
            <template #default="{ row }">{{ row.connection?.name || `${row.target_host}:${row.target_port}` }}</template>
          </el-table-column>
          <el-table-column label="本地 → 远程" min-width="220">
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
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="connVisible" :title="connForm.id ? '编辑远程连接' : '新建远程连接'" width="520px">
      <el-form label-width="96px">
        <el-form-item label="名称">
          <el-input v-model="connForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="地址">
          <el-input v-model="connForm.host" placeholder="远程 MySQL 主机" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="connForm.port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="账号">
          <el-input v-model="connForm.user" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="connForm.password" type="password" show-password :placeholder="connForm.id ? '不改请留空' : ''" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="connForm.remark" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="testing" @click="testDraft">测试连接</el-button>
        <el-button @click="connVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingConn" @click="saveConnection">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="remoteDbVisible" title="在远程新建数据库" width="460px">
      <el-form label-width="96px">
        <el-form-item label="连接">{{ remoteDbTarget?.name }}</el-form-item>
        <el-form-item label="库名">
          <el-input v-model="remoteDbForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="字符集">
          <el-select v-model="remoteDbForm.charset" style="width: 100%">
            <el-option label="utf8mb4" value="utf8mb4" />
            <el-option label="utf8" value="utf8" />
            <el-option label="latin1" value="latin1" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="remoteDbVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingRemoteDb" @click="createRemoteDb">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="visible" title="新建同步策略" width="560px">
      <el-alert type="warning" :closable="false" show-icon class="mb-3">
        仅允许本机业务库同步到已配置的远程连接，不能从远程拉回本地。
      </el-alert>
      <el-form label-width="96px">
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="远程连接">
          <el-select v-model="form.connection_id" filterable style="width: 100%" placeholder="选择已保存的远程连接" @change="onPolicyConnectionChange">
            <el-option
              v-for="item in connections"
              :key="item.id"
              :label="`${item.name} (${item.host}:${item.port})`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="本地库">
          <el-select v-model="form.source_database" filterable style="width: 100%" placeholder="本机待同步的库">
            <el-option v-for="name in databases" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
        <el-form-item label="远程库">
          <el-select
            v-model="form.target_database"
            filterable
            style="width: 100%"
            :placeholder="targetDatabases.length ? '写入远程的目标库' : '先选择远程连接'"
            :disabled="!targetDatabases.length"
          >
            <el-option v-for="item in targetDatabases" :key="item.name || item" :label="item.name || item" :value="item.name || item" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { dtsApi } from '@/api'
import { formatDateTime } from '@/utils/datetime'

const VALID_TABS = new Set(['connections', 'policies'])
const route = useRoute()
const router = useRouter()

function tabFromRoute() {
  const tab = String(route.query.tab || 'connections')
  return VALID_TABS.has(tab) ? tab : 'connections'
}

const loading = ref(false)
const saving = ref(false)
const savingConn = ref(false)
const savingRemoteDb = ref(false)
const testing = ref(false)
const visible = ref(false)
const connVisible = ref(false)
const remoteDbVisible = ref(false)
const activeTab = ref(tabFromRoute())
const items = ref([])
const connections = ref([])
const databases = ref([])
const targetDatabases = ref([])
const remoteDbMap = reactive({})
const remoteDbTarget = ref(null)
const form = reactive({
  name: '',
  connection_id: null,
  source_database: '',
  target_database: '',
})
const connForm = reactive({
  id: null,
  name: '',
  host: '',
  port: 3306,
  user: '',
  password: '',
  remark: '',
})
const remoteDbForm = reactive({
  name: '',
  charset: 'utf8mb4',
})
let timer = null

watch(
  () => route.query.tab,
  () => {
    activeTab.value = tabFromRoute()
  },
)

function onTabChange(name) {
  if (route.query.tab === name) return
  router.replace({ path: '/dts', query: { ...route.query, tab: name } })
}

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

async function loadConnections() {
  const { data } = await dtsApi.connections()
  connections.value = data.items || []
}

async function loadTasks(silent = false) {
  if (!silent) loading.value = true
  try {
    const { data } = await dtsApi.tasks()
    items.value = data.items || []
  } finally {
    loading.value = false
  }
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    await Promise.all([loadConnections(), loadTasks(true)])
  } finally {
    loading.value = false
  }
}

async function reloadCurrent() {
  if (activeTab.value === 'connections') {
    await loadConnections()
  } else {
    await loadTasks(false)
  }
}

function openConnectionDialog(row) {
  connForm.id = row?.id || null
  connForm.name = row?.name || ''
  connForm.host = row?.host || ''
  connForm.port = row?.port || 3306
  connForm.user = row?.user || ''
  connForm.password = ''
  connForm.remark = row?.remark || ''
  connVisible.value = true
}

async function testDraft() {
  testing.value = true
  try {
    const payload = {
      target_host: connForm.host,
      target_port: connForm.port,
      target_user: connForm.user,
      password: connForm.password,
    }
    if (connForm.id && !connForm.password) {
      const { data } = await dtsApi.testSavedConnection(connForm.id)
      ElMessage.success(`连接成功，MySQL ${data.version || ''}`)
      return
    }
    const { data } = await dtsApi.testConnection(payload)
    ElMessage.success(`连接成功，MySQL ${data.version || ''}，业务库 ${data.databases?.length || 0} 个`)
  } finally {
    testing.value = false
  }
}

async function saveConnection() {
  if (!connForm.name || !connForm.host || !connForm.user) {
    ElMessage.warning('请填写名称、地址和账号')
    return
  }
  if (!connForm.id && !connForm.password) {
    ElMessage.warning('请填写密码')
    return
  }
  savingConn.value = true
  try {
    const payload = {
      name: connForm.name,
      host: connForm.host,
      port: connForm.port,
      user: connForm.user,
      password: connForm.password,
      remark: connForm.remark,
    }
    if (connForm.id) {
      await dtsApi.updateConnection(connForm.id, payload)
      ElMessage.success('连接已更新')
    } else {
      await dtsApi.createConnection(payload)
      ElMessage.success('连接已保存')
    }
    connVisible.value = false
    await loadConnections()
  } finally {
    savingConn.value = false
  }
}

async function testSaved(row) {
  const { data } = await dtsApi.testSavedConnection(row.id)
  ElMessage.success(`连接成功，MySQL ${data.version || ''}，业务库 ${data.databases?.length || 0} 个`)
  remoteDbMap[row.id] = data.database_items || []
  await loadConnections()
}

async function loadRemoteDatabases(row) {
  const { data } = await dtsApi.connectionDatabases(row.id)
  remoteDbMap[row.id] = data.items || []
}

function onExpandConnection(row, expandedRows) {
  if (expandedRows.some((item) => item.id === row.id) && !remoteDbMap[row.id]) {
    loadRemoteDatabases(row)
  }
}

function openRemoteDbDialog(row) {
  remoteDbTarget.value = row
  remoteDbForm.name = ''
  remoteDbForm.charset = 'utf8mb4'
  remoteDbVisible.value = true
}

async function createRemoteDb() {
  if (!remoteDbTarget.value || !remoteDbForm.name) {
    ElMessage.warning('请填写库名')
    return
  }
  savingRemoteDb.value = true
  try {
    await dtsApi.createRemoteDatabase(remoteDbTarget.value.id, { ...remoteDbForm })
    ElMessage.success('远程库已创建')
    remoteDbVisible.value = false
    await loadRemoteDatabases(remoteDbTarget.value)
  } finally {
    savingRemoteDb.value = false
  }
}

async function removeConnection(row) {
  try {
    await ElMessageBox.confirm(`删除远程连接「${row.name}」？`, '删除连接', { type: 'warning' })
  } catch {
    return
  }
  await dtsApi.removeConnection(row.id)
  ElMessage.success('已删除')
  await loadConnections()
}

async function openCreate() {
  if (!connections.value.length) {
    await loadConnections()
  }
  if (!connections.value.length) {
    ElMessage.warning('请先在「远程连接」中添加一台远程 MySQL')
    activeTab.value = 'connections'
    onTabChange('connections')
    return
  }
  const { data } = await dtsApi.databases()
  databases.value = data.items || []
  form.name = ''
  form.connection_id = connections.value[0]?.id || null
  form.source_database = ''
  form.target_database = ''
  targetDatabases.value = []
  visible.value = true
  if (form.connection_id) await onPolicyConnectionChange(form.connection_id)
}

async function onPolicyConnectionChange(id) {
  form.target_database = ''
  if (!id) {
    targetDatabases.value = []
    return
  }
  const { data } = await dtsApi.connectionDatabases(id)
  targetDatabases.value = data.items || []
}

async function create() {
  if (!form.connection_id || !form.source_database || !form.target_database) {
    ElMessage.warning('请选择远程连接、本地库和远程库')
    return
  }
  saving.value = true
  try {
    await dtsApi.createTask({ ...form })
    ElMessage.success('同步策略已创建')
    visible.value = false
    await loadTasks(true)
  } finally {
    saving.value = false
  }
}

async function start(row, full) {
  if (!row.binlog_file || full) {
    try {
      await ElMessageBox.confirm(
        full
          ? `重新全量会把本地库 ${row.source_database} 覆盖写入远程 ${row.target_database}，同名表会被替换。确认继续？`
          : `首次启动会把本地库 ${row.source_database} 全量同步到远程 ${row.target_database}，覆盖其中的同名表，完成后自动进入增量。确认启动？`,
        '确认同步',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  await dtsApi.action(row.id, 'start', { full })
  ElMessage.success(full || !row.binlog_file ? '已开始全量同步' : '已继续增量同步')
  await loadTasks(true)
}

async function restartFull(row) {
  await start(row, true)
}

async function act(row, action) {
  await dtsApi.action(row.id, action)
  await loadTasks(true)
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除同步策略「${row.name}」？已同步到远程的数据不会回滚。`, '删除策略', { type: 'warning' })
  } catch {
    return
  }
  await dtsApi.removeTask(row.id)
  await loadTasks(true)
}

onMounted(() => {
  load(false)
  timer = setInterval(() => loadTasks(true), 3000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 8px; }
.page-title { margin: 0 0 4px; }
.page-hint { color: #909399; font-size: 13px; }
.pane-actions { margin-bottom: 12px; }
.mb-3 { margin-bottom: 16px; }
.db-panel { padding: 4px 12px 12px 48px; }
.db-panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 13px; color: #606266; }
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
