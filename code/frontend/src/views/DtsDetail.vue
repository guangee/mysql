<template>
  <div v-loading="loading">
    <div class="toolbar">
      <div>
        <el-button link type="primary" @click="router.push('/dts')">返回列表</el-button>
        <h2 class="page-title">{{ task.name || '同步详情' }}</h2>
        <div class="page-hint" v-if="task.id">
          {{ task.source_database || '-' }} → {{ task.connection?.name || `${task.target_host}:${task.target_port}` }}/{{ task.target_database || '-' }}
        </div>
      </div>
      <div class="actions">
        <el-tag v-if="task.status" :type="statusType(task.status)">{{ task.status_display }}</el-tag>
        <el-button :icon="Refresh" circle @click="load(false)" />
      </div>
    </div>

    <el-steps :active="stepActive" finish-status="success" align-center class="steps">
      <el-step title="同步表结构" :description="structureDesc" />
      <el-step title="逐表全量" :description="dataDesc" />
      <el-step title="增量同步" :description="incrementalDesc" />
    </el-steps>

    <div class="summary">
      <span>{{ task.table_total || 0 }} 张表 · {{ formatRows(task.rows_total) }} · {{ formatBytes(task.bytes_total) }}</span>
      <el-progress :percentage="task.progress_percent || 0" :status="progressStatus(task)" :stroke-width="10" />
      <span class="muted">{{ progressText }}</span>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane :label="sqlTabLabel" name="sql">
        <div class="page-hint sql-hint">仅保留最近 {{ sqlLimit }} 条增量 SQL（Redis 内存，控制台重建后清空）。当前展示 {{ sqlEvents.length }} 条。</div>
        <el-table :data="sqlEvents" stripe max-height="560" empty-text="暂无增量 SQL 明细">
          <el-table-column prop="at" label="时间" width="170" />
          <el-table-column label="类型" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="sqlKindType(row)">{{ row.kind || '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="table" label="表" width="180">
            <template #default="{ row }">{{ row.table || '-' }}</template>
          </el-table-column>
          <el-table-column label="位点" width="220">
            <template #default="{ row }">{{ row.file ? `${row.file}:${row.pos || 0}` : '-' }}</template>
          </el-table-column>
          <el-table-column label="SQL" min-width="360">
            <template #default="{ row }">
              <pre class="sql-cell">{{ row.sql || '-' }}</pre>
              <div v-if="row.error" class="error-line">{{ row.error }}</div>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="运行日志" name="logs">
        <div v-if="task.error_message" class="error-line block">{{ task.error_message }}</div>
        <pre class="log">{{ displayLog }}</pre>
      </el-tab-pane>

      <el-tab-pane :label="tablesTabLabel" name="tables">
        <div class="filter-row">
          <el-input v-model="keyword" clearable placeholder="筛选表名" style="width: 240px" />
          <el-radio-group v-model="phaseFilter">
            <el-radio-button value="">全部</el-radio-button>
            <el-radio-button value="pending">等待</el-radio-button>
            <el-radio-button value="structure">结构已同步</el-radio-button>
            <el-radio-button value="copying">同步数据</el-radio-button>
            <el-radio-button value="done">已完成</el-radio-button>
            <el-radio-button value="error">失败</el-radio-button>
          </el-radio-group>
        </div>

        <el-table :data="filteredTables" stripe max-height="560" row-key="id">
          <el-table-column prop="name" label="表名" min-width="220" />
          <el-table-column label="阶段" width="120">
            <template #default="{ row }">
              <el-tag size="small" :type="phaseType(row.phase)">{{ row.phase_display }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="预估行数" width="120">
            <template #default="{ row }">{{ formatRows(row.rows_total).replace('约 ', '') }}</template>
          </el-table-column>
          <el-table-column label="数据量" width="120">
            <template #default="{ row }">{{ formatBytes(row.bytes_total) }}</template>
          </el-table-column>
          <el-table-column label="进度" min-width="220">
            <template #default="{ row }">
              <el-progress :percentage="row.percent || 0" :status="row.phase === 'error' ? 'exception' : (row.phase === 'done' ? 'success' : '')" :stroke-width="8" />
              <div v-if="row.error_message" class="error-line">{{ row.error_message }}</div>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { dtsApi } from '@/api'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const task = ref({})
const tables = ref([])
const sqlEvents = ref([])
const sqlTotal = ref(0)
const sqlLimit = ref(10000)
const keyword = ref('')
const phaseFilter = ref('')
const activeTab = ref('sql')
let timer = null

const filteredTables = computed(() => {
  const word = keyword.value.trim().toLowerCase()
  return tables.value.filter((row) => {
    if (phaseFilter.value && row.phase !== phaseFilter.value) return false
    if (word && !String(row.name).toLowerCase().includes(word)) return false
    return true
  })
})

const sqlTabLabel = computed(() => {
  if (sqlTotal.value) return `增量 SQL (${sqlTotal.value})`
  return '增量 SQL'
})

const tablesTabLabel = computed(() => {
  const total = task.value.table_total || tables.value.length || 0
  return total ? `表同步详情 (${total})` : '表同步详情'
})

const displayLog = computed(() => {
  const text = (task.value.log_text || '').trim()
  if (!text) return '暂无日志'
  return text.split('\n').reverse().join('\n')
})

const stepActive = computed(() => {
  if (task.value.status === 'incremental') return 3
  if (task.value.full_phase === 'data' || task.value.full_phase === 'done') return 1
  return 0
})

const structureDesc = computed(() => {
  if (task.value.full_phase === 'export') return '正在导出快照'
  return `${task.value.structure_done || 0}/${task.value.table_total || 0}`
})

const dataDesc = computed(() => `${task.value.table_done || 0}/${task.value.table_total || 0}`)

const incrementalDesc = computed(() => {
  if (task.value.status !== 'incremental') return '等待全量完成'
  if (task.value.lag_seconds == null) return '同步中'
  return `延迟 ${task.value.lag_seconds} 秒`
})

const progressText = computed(() => {
  if (task.value.status === 'incremental') return '全量完成，增量同步中'
  if (task.value.full_phase === 'export') return '正在导出一致性快照'
  if (task.value.full_phase === 'structure') return `正在同步表结构 ${task.value.current_table || ''}`.trim()
  if (task.value.full_phase === 'data') return `正在全量同步 ${task.value.current_table || ''}`.trim()
  return '尚未开始'
})

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

function statusType(status) {
  return { incremental: 'success', full_sync: 'primary', paused: 'warning', error: 'danger', stopped: 'info' }[status] || 'info'
}

function progressStatus(row) {
  if (row.status === 'error') return 'exception'
  if (row.status === 'incremental' || row.progress_percent >= 100) return 'success'
  if (row.status === 'paused') return 'warning'
  return ''
}

function phaseType(phase) {
  return { pending: 'info', structure: 'warning', copying: 'primary', done: 'success', error: 'danger' }[phase] || 'info'
}

function sqlKindType(row) {
  if (row.ok === false) return 'danger'
  return { INSERT: 'success', UPDATE: 'warning', DELETE: 'danger', DDL: 'primary', ERROR: 'danger' }[row.kind] || 'info'
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const [{ data }, events] = await Promise.all([
      dtsApi.detail(route.params.id),
      dtsApi.sqlEvents(route.params.id, 200),
    ])
    task.value = data.task || {}
    tables.value = data.tables || []
    sqlEvents.value = events.data?.items || []
    sqlTotal.value = events.data?.total || 0
    sqlLimit.value = events.data?.limit || 10000
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load(false)
  timer = setInterval(() => load(true), 3000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }
.page-title { margin: 4px 0; }
.page-hint, .muted { color: #909399; font-size: 13px; }
.actions { display: flex; gap: 8px; align-items: center; }
.steps { margin: 8px 0 20px; }
.summary { display: grid; gap: 8px; margin-bottom: 16px; }
.filter-row { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.error-line { color: #f56c6c; font-size: 12px; }
.block { margin-bottom: 12px; }
.log {
  margin: 0;
  padding: 12px 14px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  max-height: min(62vh, 640px);
  overflow: auto;
}
.sql-hint { margin-bottom: 12px; }
.sql-cell {
  margin: 0;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 160px;
  overflow: auto;
}
:deep(.el-progress__text) { font-size: 12px !important; }
</style>
