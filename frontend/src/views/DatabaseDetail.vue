<template>
  <div v-loading="loading">
    <div class="page-header">
      <div class="header-left">
        <el-button link type="primary" @click="router.push('/databases')">← 返回列表</el-button>
        <h2 class="page-title"><code>{{ dbName }}</code></h2>
        <div v-if="database" class="db-meta">
          {{ database.charset }} / {{ database.collation }} · {{ database.table_count }} 表 · {{ database.size_display }}
          <span v-if="collectedAt"> · 更新于 {{ formatDateTime(collectedAt) }}</span>
        </div>
      </div>
      <el-button :loading="loading" @click="loadAll(true)">刷新</el-button>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="表结构" name="tables">
        <div class="table-toolbar">
          <span class="muted">默认按总大小降序；可切换排序字段</span>
          <el-radio-group v-model="tableSort" size="small">
            <el-radio-button value="size_bytes">总大小</el-radio-button>
            <el-radio-button value="data_bytes">数据</el-radio-button>
            <el-radio-button value="index_bytes">索引</el-radio-button>
            <el-radio-button value="row_count">行数</el-radio-button>
            <el-radio-button value="name">表名</el-radio-button>
          </el-radio-group>
        </div>
        <el-table :data="sortedTables" stripe border @row-click="openStructure">
          <el-table-column prop="name" label="表名" min-width="150">
            <template #default="{ row }"><code>{{ row.name }}</code></template>
          </el-table-column>
          <el-table-column prop="engine" label="引擎" width="90" />
          <el-table-column prop="row_count" label="行数(约)" width="100" align="right" />
          <el-table-column prop="data_size_display" label="数据" width="100" align="right" />
          <el-table-column prop="index_size_display" label="索引" width="100" align="right" />
          <el-table-column prop="size_display" label="总大小" width="100" align="right" />
          <el-table-column label="库内占比" width="140">
            <template #default="{ row }">
              <el-progress
                :percentage="Math.min(Number(row.size_percent) || 0, 100)"
                :stroke-width="8"
                :show-text="true"
              />
            </template>
          </el-table-column>
          <el-table-column prop="comment" label="注释" min-width="120" show-overflow-tooltip />
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click.stop="openStructure(row)">结构</el-button>
              <el-button size="small" link @click.stop="previewTable(row)">预览</el-button>
              <el-button size="small" link @click.stop="exportTable(row)">导出 Excel</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="资源监控" name="resources">
        <div class="resource-toolbar">
          <el-radio-group v-model="metricHours" size="small" @change="loadMetrics">
            <el-radio-button :value="1">近 1 小时</el-radio-button>
            <el-radio-button :value="6">近 6 小时</el-radio-button>
            <el-radio-button :value="24">近 24 小时</el-radio-button>
          </el-radio-group>
          <span class="muted">采样间隔约 3 秒 · 类似 RDS 监控面板</span>
        </div>

        <el-alert type="info" :closable="false" show-icon class="mb-3">
          <template #title>
            {{ (resourceNotes && resourceNotes[0]) || '内存与算力为近似分摊；QPS 来自 Performance Schema 采样差分。' }}
          </template>
        </el-alert>

        <el-row :gutter="12" class="mb-3">
          <el-col :span="4" :xs="12" :sm="8" :md="4" v-for="card in kpiCards" :key="card.label">
            <el-card shadow="never" class="kpi-card">
              <div class="stat-label">{{ card.label }}</div>
              <div class="stat-value">{{ card.value }}</div>
              <div class="stat-sub">{{ card.sub }}</div>
            </el-card>
          </el-col>
        </el-row>

        <el-row :gutter="12" class="mb-3">
          <el-col :span="12" :xs="24">
            <el-card shadow="never" class="chart-card">
              <MetricLineChart title="CPU / 内存近似占用" :series="cpuMemSeries" y-axis-left-name="%" :height="260" />
            </el-card>
          </el-col>
          <el-col :span="12" :xs="24">
            <el-card shadow="never" class="chart-card">
              <MetricLineChart title="连接与活跃会话" :series="sessionSeries" y-axis-left-name="连接数" :height="260" />
            </el-card>
          </el-col>
        </el-row>

        <el-row :gutter="12" class="mb-3">
          <el-col :span="12" :xs="24">
            <el-card shadow="never" class="chart-card">
              <MetricLineChart
                title="存储空间（数据 / 索引）"
                :series="storageSeries"
                y-axis-left-name="MB"
                :height="260"
              />
            </el-card>
          </el-col>
          <el-col :span="12" :xs="24">
            <el-card shadow="never" class="chart-card">
              <MetricLineChart
                title="QPS / 扫描行速率"
                :series="qpsSeries"
                y-axis-left-name="QPS"
                y-axis-right-name="行/秒"
                :height="260"
              />
            </el-card>
          </el-col>
        </el-row>

        <el-row :gutter="12" class="mb-3">
          <el-col :span="12" :xs="24">
            <el-card shadow="never" class="chart-card">
              <MetricLineChart
                title="平均延迟 / 未用索引语句速率"
                :series="latencySeries"
                y-axis-left-name="ms"
                y-axis-right-name="次/秒"
                :height="260"
              />
            </el-card>
          </el-col>
          <el-col :span="12" :xs="24">
            <el-card shadow="never" class="chart-card">
              <MetricLineChart
                title="实例环境（容器 CPU / 内存）"
                :series="instanceSeries"
                y-axis-left-name="%"
                :height="260"
              />
            </el-card>
          </el-col>
        </el-row>

        <el-card shadow="never" class="mb-3">
          <template #header>存储构成</template>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="数据空间">{{ database?.data_size_display || '-' }}</el-descriptions-item>
            <el-descriptions-item label="索引空间">{{ database?.index_size_display || '-' }}</el-descriptions-item>
            <el-descriptions-item label="可回收碎片">{{ database?.data_free_display || formatSize(monitors?.storage?.data_free_bytes) }}</el-descriptions-item>
            <el-descriptions-item label="数据占比">{{ formatPercent(monitors?.storage?.data_ratio_percent ?? database?.data_ratio_percent) }}</el-descriptions-item>
            <el-descriptions-item label="索引占比">{{ formatPercent(monitors?.storage?.index_ratio_percent ?? database?.index_ratio_percent) }}</el-descriptions-item>
            <el-descriptions-item label="表数量">{{ database?.table_count ?? tableTotal }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card v-if="instanceContext?.host_stats || instanceContext?.mysql_connections" shadow="never">
          <template #header>实例上下文</template>
          <el-row :gutter="12">
            <el-col v-for="item in instanceProgressCards" :key="item.label" :span="8" :xs="24" :sm="12" :md="8">
              <div class="progress-block">
                <div class="progress-head">
                  <span class="progress-label">{{ item.label }}</span>
                  <strong class="progress-value">{{ item.display }}</strong>
                </div>
                <el-progress
                  :percentage="item.percent"
                  :stroke-width="10"
                  :status="item.status"
                  :format="() => `${item.percent}%`"
                />
                <div class="progress-sub">{{ item.sub }}</div>
              </div>
            </el-col>
          </el-row>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="结构变更" name="changes">
        <el-table :data="schemaChanges" stripe border empty-text="暂无结构变更记录">
          <el-table-column label="时间" width="180">
            <template #default="{ row }">{{ formatDateTime(row.detected_at) }}</template>
          </el-table-column>
          <el-table-column prop="table_name" label="表" min-width="140">
            <template #default="{ row }"><code>{{ row.table_name || '-' }}</code></template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <el-tag :type="changeTagType(row.change_type)" size="small">{{ row.change_type_display }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="openDiff(row)">查看 Diff</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="SQL 查询" name="sql">
        <el-alert type="info" :closable="false" show-icon class="sql-tip">
          仅支持 SELECT / SHOW / DESCRIBE / EXPLAIN，单次最多返回 5000 行。
        </el-alert>
        <el-input
          v-model="sqlText"
          type="textarea"
          :rows="8"
          placeholder="SELECT * FROM your_table LIMIT 100"
          class="sql-editor"
        />
        <div class="sql-toolbar">
          <el-input-number v-model="queryLimit" :min="1" :max="5000" :step="100" />
          <span class="limit-label">行上限</span>
          <el-button type="primary" :loading="querying" @click="runQuery">执行</el-button>
          <el-button :disabled="!queryResult.columns?.length" :loading="exporting" @click="exportQuery">
            导出结果为 Excel
          </el-button>
        </div>
        <div v-if="queryResult.columns?.length" class="result-meta">
          返回 {{ queryResult.row_count }} 行
          <el-tag v-if="queryResult.truncated" size="small" type="warning" class="ml-1">已截断</el-tag>
        </div>
        <el-table v-if="queryResult.columns?.length" :data="resultRows" stripe border max-height="480" class="result-table">
          <el-table-column
            v-for="col in queryResult.columns"
            :key="col"
            :prop="col"
            :label="col"
            min-width="120"
            show-overflow-tooltip
          />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="structureVisible" :title="`表结构：${structure?.table || ''}`" size="640px">
      <template v-if="structure">
        <h4 class="section-title">字段</h4>
        <el-table :data="structure.columns" stripe size="small" max-height="320">
          <el-table-column prop="name" label="字段" width="130" />
          <el-table-column prop="type" label="类型" width="140" />
          <el-table-column label="可空" width="60" align="center">
            <template #default="{ row }">{{ row.nullable ? '是' : '否' }}</template>
          </el-table-column>
          <el-table-column prop="key" label="键" width="60" />
          <el-table-column prop="default" label="默认" width="90" show-overflow-tooltip />
          <el-table-column prop="extra" label="Extra" width="100" />
          <el-table-column prop="comment" label="注释" min-width="120" show-overflow-tooltip />
        </el-table>

        <h4 class="section-title">索引</h4>
        <el-table :data="structure.indexes" stripe size="small" empty-text="无索引">
          <el-table-column prop="name" label="名称" width="160" />
          <el-table-column label="唯一" width="70" align="center">
            <template #default="{ row }">{{ row.unique ? '是' : '否' }}</template>
          </el-table-column>
          <el-table-column prop="type" label="类型" width="80" />
          <el-table-column label="列">
            <template #default="{ row }">{{ row.columns.join(', ') }}</template>
          </el-table-column>
        </el-table>

        <h4 v-if="structure.create_sql" class="section-title">建表语句</h4>
        <pre v-if="structure.create_sql" class="create-sql">{{ structure.create_sql }}</pre>

        <h4 class="section-title">最近变更</h4>
        <el-table :data="structure.recent_changes || []" stripe size="small" empty-text="暂无变更">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ formatDateTime(row.detected_at) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="80">
            <template #default="{ row }">{{ row.change_type_display }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="openDiff(row)">Diff</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>

    <el-dialog v-model="diffVisible" title="结构 Diff" width="720px" destroy-on-close>
      <div v-if="activeDiff" class="diff-meta">
        <el-tag :type="changeTagType(activeDiff.change_type)" size="small">{{ activeDiff.change_type_display }}</el-tag>
        <code class="ml-1">{{ activeDiff.table_name }}</code>
        <span class="muted ml-1">{{ formatDateTime(activeDiff.detected_at) }}</span>
      </div>
      <pre class="diff-body">{{ activeDiff?.unified_diff || '（无文本差异）' }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { databaseApi } from '@/api'
import MetricLineChart from '@/components/MetricLineChart.vue'
import { formatDateTime } from '@/utils/datetime'

const route = useRoute()
const router = useRouter()
const dbName = computed(() => route.params.name)

const loading = ref(false)
const querying = ref(false)
const exporting = ref(false)
const activeTab = ref('tables')
const database = ref(null)
const tables = ref([])
const tableTotal = ref(0)
const collectedAt = ref('')
const tableSort = ref('size_bytes')
const schemaChanges = ref([])
const metricPoints = ref([])
const instancePoints = ref([])
const monitors = ref(null)
const instanceContext = ref(null)
const resourceNotes = ref([])
const metricHours = ref(6)
const structureVisible = ref(false)
const structure = ref(null)
const diffVisible = ref(false)
const activeDiff = ref(null)
const sqlText = ref('')
const queryLimit = ref(500)
const queryResult = reactive({ columns: [], rows: [], row_count: 0, truncated: false })

const sortedTables = computed(() => {
  const rows = [...(tables.value || [])]
  const key = tableSort.value
  rows.sort((a, b) => {
    if (key === 'name') return String(a.name).localeCompare(String(b.name))
    return Number(b[key] || 0) - Number(a[key] || 0)
  })
  return rows
})

const latestPoint = computed(() => {
  const points = metricPoints.value || []
  return points.length ? points[points.length - 1] : null
})

const kpiCards = computed(() => {
  const db = database.value || {}
  const mon = monitors.value?.compute || {}
  const sess = monitors.value?.sessions || {}
  const p = latestPoint.value || {}
  return [
    {
      label: '近似 CPU',
      value: formatPercent(p.cpu_share_percent ?? mon.cpu_share_percent ?? db.cpu_share_percent),
      sub: `份额 ${formatPercent(p.compute_share_percent ?? mon.compute_share_percent ?? db.compute_share_percent)}`,
    },
    {
      label: '近似内存',
      value: db.memory_share_display || formatSize(p.memory_share_bytes ?? mon.memory_share_bytes),
      sub: `占比 ${formatPercent(p.memory_share_percent ?? mon.memory_share_percent ?? db.memory_share_percent)}`,
    },
    {
      label: '总存储',
      value: db.size_display || formatSize(p.size_bytes),
      sub: `数据 ${db.data_size_display || '-'} / 索引 ${db.index_size_display || '-'}`,
    },
    {
      label: '连接数',
      value: String(p.connection_count ?? sess.connections ?? db.connection_count ?? 0),
      sub: `活跃 ${p.running_sessions ?? sess.running ?? db.running_sessions ?? 0} · 空闲 ${p.sleeping_sessions ?? sess.sleeping ?? db.sleeping_sessions ?? 0}`,
    },
    {
      label: 'QPS',
      value: formatNumber(p.qps ?? mon.qps ?? db.qps),
      sub: `延迟 ${formatNumber(p.avg_latency_ms ?? mon.avg_latency_ms ?? db.avg_latency_ms)} ms`,
    },
    {
      label: '扫描行/秒',
      value: formatNumber(p.rows_examined_per_sec ?? mon.rows_examined_per_sec ?? db.rows_examined_per_sec),
      sub: `返回 ${formatNumber(p.rows_sent_per_sec ?? mon.rows_sent_per_sec ?? db.rows_sent_per_sec)} 行/秒`,
    },
  ]
})

function seriesFrom(points, key, scale = 1) {
  return (points || []).map((p) => [p.ts * 1000, Number((((p[key] || 0) / scale)).toFixed(3))])
}

const cpuMemSeries = computed(() => [
  { name: '近似 CPU%', data: seriesFrom(metricPoints.value, 'cpu_share_percent') },
  { name: '近似内存%', data: seriesFrom(metricPoints.value, 'memory_share_percent') },
  { name: '算力份额%', data: seriesFrom(metricPoints.value, 'compute_share_percent') },
])

const sessionSeries = computed(() => [
  { name: '连接数', data: seriesFrom(metricPoints.value, 'connection_count') },
  { name: '活跃会话', data: seriesFrom(metricPoints.value, 'running_sessions') },
  { name: '空闲会话', data: seriesFrom(metricPoints.value, 'sleeping_sessions') },
])

const storageSeries = computed(() => [
  { name: '数据(MB)', data: seriesFrom(metricPoints.value, 'data_bytes', 1024 * 1024) },
  { name: '索引(MB)', data: seriesFrom(metricPoints.value, 'index_bytes', 1024 * 1024) },
  { name: '总计(MB)', data: seriesFrom(metricPoints.value, 'size_bytes', 1024 * 1024) },
])

const qpsSeries = computed(() => [
  { name: 'QPS', data: seriesFrom(metricPoints.value, 'qps') },
  { name: '扫描行/秒', yAxisIndex: 1, data: seriesFrom(metricPoints.value, 'rows_examined_per_sec') },
  { name: '返回行/秒', yAxisIndex: 1, data: seriesFrom(metricPoints.value, 'rows_sent_per_sec') },
])

const latencySeries = computed(() => [
  { name: '平均延迟(ms)', data: seriesFrom(metricPoints.value, 'avg_latency_ms') },
  { name: '未用索引/秒', yAxisIndex: 1, data: seriesFrom(metricPoints.value, 'no_index_used_per_sec') },
  { name: '错误/秒', yAxisIndex: 1, data: seriesFrom(metricPoints.value, 'errors_per_sec') },
])

const instanceSeries = computed(() => [
  { name: '容器 CPU%', data: seriesFrom(instancePoints.value, 'cpu_percent') },
  { name: '容器内存%', data: seriesFrom(instancePoints.value, 'memory_usage_percent') },
  { name: '宿主机内存%', data: seriesFrom(instancePoints.value, 'host_memory_usage_percent') },
])

const instanceProgressCards = computed(() => {
  const ctx = instanceContext.value || {}
  const conn = ctx.mysql_connections || {}
  const memory = ctx.mysql_memory || {}
  const host = ctx.host_stats || {}
  const storage = ctx.mysql_storage || {}

  const connPercent = clampPercent(
    conn.usage_percent != null
      ? conn.usage_percent
      : ratioPercent(conn.current, conn.max_limit),
  )
  const bufferPercent = clampPercent(
    memory.innodb_buffer_pool_usage_percent != null
      ? memory.innodb_buffer_pool_usage_percent
      : ratioPercent(memory.innodb_buffer_pool_used_bytes, memory.innodb_buffer_pool_size_bytes),
  )
  const cpuPercent = clampPercent(host.cpu_percent)
  const memPercent = clampPercent(
    host.memory_usage_percent != null
      ? host.memory_usage_percent
      : ratioPercent(host.memory_used_bytes, host.memory_total_bytes),
  )
  const diskPercent = clampPercent(
    host.disk_usage_percent != null
      ? host.disk_usage_percent
      : ratioPercent(host.disk_used_bytes, host.disk_total_bytes),
  )
  const businessBytes = Number(storage.data_business_bytes || 0)
  const totalBytes = Number(storage.data_total_bytes || 0)
  const businessPercent = clampPercent(ratioPercent(businessBytes, totalBytes || businessBytes))

  return [
    {
      label: '实例连接',
      percent: connPercent,
      display: `${conn.current ?? '-'} / ${conn.max_limit ?? '-'}`,
      sub: `活跃 ${conn.running ?? '-'} · 峰值 ${conn.max_used ?? '-'}`,
      status: progressStatus(connPercent),
    },
    {
      label: 'InnoDB 缓冲池',
      percent: bufferPercent,
      display: `${formatSize(memory.innodb_buffer_pool_used_bytes)} / ${formatSize(memory.innodb_buffer_pool_size_bytes)}`,
      sub: `使用率 ${bufferPercent}%`,
      status: progressStatus(bufferPercent, true),
    },
    {
      label: '容器 CPU',
      percent: cpuPercent,
      display: `${cpuPercent}%`,
      sub: `负载 ${host.load_1m ?? '-'}`,
      status: progressStatus(cpuPercent),
    },
    {
      label: '容器内存',
      percent: memPercent,
      display: `${formatSize(host.memory_used_bytes)} / ${formatSize(host.memory_total_bytes)}`,
      sub: `使用率 ${memPercent}%`,
      status: progressStatus(memPercent),
    },
    {
      label: '磁盘',
      percent: diskPercent,
      display: `${formatSize(host.disk_used_bytes)} / ${formatSize(host.disk_total_bytes)}`,
      sub: `可用 ${formatSize(host.disk_available_bytes)}`,
      status: progressStatus(diskPercent),
    },
    {
      label: '业务数据占比',
      percent: businessPercent,
      display: formatSize(businessBytes),
      sub: `全部数据 ${formatSize(totalBytes)}`,
      status: 'success',
    },
  ]
})

const resultRows = computed(() =>
  queryResult.rows.map((row) => {
    const obj = {}
    queryResult.columns.forEach((col, i) => {
      obj[col] = row[i]
    })
    return obj
  }),
)

function clampPercent(value) {
  const n = Number(value)
  if (Number.isNaN(n) || n == null) return 0
  return Math.max(0, Math.min(100, Math.round(n * 10) / 10))
}

function ratioPercent(used, total) {
  const u = Number(used)
  const t = Number(total)
  if (!t || Number.isNaN(u) || Number.isNaN(t)) return 0
  return (u / t) * 100
}

function progressStatus(percent, preferSuccess = false) {
  if (percent >= 90) return 'exception'
  if (percent >= 75) return 'warning'
  if (preferSuccess) return 'success'
  return undefined
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(1)}%`
}

function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  const n = Number(value)
  if (n >= 1000) return n.toFixed(0)
  if (n >= 10) return n.toFixed(1)
  return n.toFixed(2)
}

function formatSize(bytes) {
  if (bytes == null) return '-'
  const n = Number(bytes)
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

function changeTagType(type) {
  return { created: 'success', altered: 'warning', dropped: 'danger' }[type] || 'info'
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function loadDetail(refresh = false) {
  const { data } = await databaseApi.detail(dbName.value, refresh)
  database.value = data.database
  tables.value = data.tables || []
  tableTotal.value = data.table_total || (data.tables || []).length
  collectedAt.value = data.collected_at || ''
  monitors.value = data.monitors || null
  instanceContext.value = data.instance_context || null
  resourceNotes.value = data.resource_notes || []
}

async function loadChanges() {
  const { data } = await databaseApi.schemaChanges(dbName.value, { limit: 100 })
  schemaChanges.value = data.items || []
}

async function loadMetrics() {
  const { data } = await databaseApi.metrics(dbName.value, metricHours.value)
  metricPoints.value = data.points || []
  instancePoints.value = data.instance_points || []
  if (data.monitors) monitors.value = data.monitors
  if (data.instance_context) instanceContext.value = data.instance_context
  if (data.resource_notes?.length) resourceNotes.value = data.resource_notes
}

async function loadAll(refresh = false) {
  loading.value = true
  try {
    await Promise.all([loadDetail(refresh), loadChanges(), loadMetrics()])
  } finally {
    loading.value = false
  }
}

async function openStructure(row) {
  const { data } = await databaseApi.tableStructure(dbName.value, row.name)
  structure.value = data
  structureVisible.value = true
}

function openDiff(row) {
  activeDiff.value = row
  diffVisible.value = true
}

function previewTable(row) {
  activeTab.value = 'sql'
  sqlText.value = `SELECT * FROM \`${row.name}\` LIMIT 100`
}

async function runQuery() {
  if (!sqlText.value.trim()) {
    ElMessage.warning('请输入 SQL')
    return
  }
  querying.value = true
  try {
    const { data } = await databaseApi.query(dbName.value, {
      sql: sqlText.value,
      limit: queryLimit.value,
    })
    queryResult.columns = data.columns || []
    queryResult.rows = data.rows || []
    queryResult.row_count = data.row_count || 0
    queryResult.truncated = data.truncated || false
  } finally {
    querying.value = false
  }
}

async function exportTable(row) {
  exporting.value = true
  try {
    const { data, headers } = await databaseApi.export(dbName.value, { table: row.name })
    const disposition = headers['content-disposition'] || ''
    const match = disposition.match(/filename=\"?([^\";]+)/i)
    saveBlob(data, match?.[1] || `${dbName.value}_${row.name}.xlsx`)
    ElMessage.success('导出已开始')
  } finally {
    exporting.value = false
  }
}

async function exportQuery() {
  if (!sqlText.value.trim()) {
    ElMessage.warning('请输入 SQL')
    return
  }
  exporting.value = true
  try {
    const { data, headers } = await databaseApi.export(dbName.value, {
      sql: sqlText.value,
      limit: queryLimit.value,
    })
    const disposition = headers['content-disposition'] || ''
    const match = disposition.match(/filename=\"?([^\";]+)/i)
    saveBlob(data, match?.[1] || `${dbName.value}_export.xlsx`)
    ElMessage.success('导出已开始')
  } finally {
    exporting.value = false
  }
}

watch(dbName, () => {
  queryResult.columns = []
  queryResult.rows = []
  loadAll()
})

let refreshTimer = null

onMounted(() => {
  loadAll()
  refreshTimer = setInterval(() => {
    if (activeTab.value === 'resources') {
      loadMetrics()
      loadDetail(false)
    } else {
      loadDetail(false)
    }
  }, 3000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.page-header {
  margin-bottom: 16px;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.header-left { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.page-title { margin: 0; font-size: 22px; }
.db-meta { font-size: 13px; color: #909399; }
.resource-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.kpi-card { min-height: 96px; margin-bottom: 0; }
.chart-card { margin-bottom: 0; }
.table-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.mb-3 { margin-bottom: 16px; }
.stat-label { color: #909399; font-size: 12px; margin-bottom: 6px; }
.stat-value { font-size: 18px; font-weight: 600; line-height: 1.3; }
.stat-sub { margin-top: 4px; color: #909399; font-size: 12px; }
.progress-block {
  padding: 8px 4px 12px;
}
.progress-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}
.progress-label { color: #606266; font-size: 13px; }
.progress-value { font-size: 13px; color: #303133; }
.progress-sub { margin-top: 6px; color: #909399; font-size: 12px; }
.sql-tip { margin-bottom: 12px; }
.sql-editor { font-family: ui-monospace, monospace; }
.sql-toolbar { display: flex; align-items: center; gap: 12px; margin: 12px 0; flex-wrap: wrap; }
.limit-label { color: #606266; font-size: 13px; }
.result-meta { margin-bottom: 8px; font-size: 13px; color: #606266; }
.result-table { width: 100%; }
.section-title { margin: 16px 0 8px; font-size: 14px; font-weight: 600; }
.create-sql, .diff-body {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  overflow: auto;
  max-height: 360px;
  white-space: pre-wrap;
  word-break: break-word;
}
.diff-meta { margin-bottom: 12px; }
.muted { color: #909399; }
.ml-1 { margin-left: 6px; }
</style>
