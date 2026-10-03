<template>
  <div v-loading="pageLoading">
    <h2 class="page-title">系统概览</h2>
    <div v-if="data.collected_at" class="page-hint">数据更新于 {{ formatDateTime(data.collected_at) }}，后台每 3 秒采样一次</div>

    <el-row :gutter="16" class="mb-4">
      <el-col :span="6" :xs="24" :sm="12" :md="6">
        <el-card shadow="never">
          <div class="stat-label">MySQL 状态</div>
          <div v-if="data.mysql_status?.connected" class="stat-value ok">
            <el-icon><CircleCheck /></el-icon> 已连接
          </div>
          <div v-else class="stat-value err">
            <el-icon><CircleClose /></el-icon> 未连接
          </div>
          <div class="stat-sub">{{ data.mysql_status?.version || data.mysql_error }}</div>
          <div v-if="data.mysql_status?.uptime_display" class="stat-sub">
            运行 {{ data.mysql_status.uptime_display }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="6" :xs="24" :sm="12" :md="6">
        <el-card shadow="never">
          <div class="stat-label">当前连接</div>
          <div class="stat-num">{{ conn.current ?? '-' }}</div>
          <div class="stat-sub">上限 {{ conn.max_limit ?? '-' }}，峰值 {{ conn.max_used ?? '-' }}</div>
        </el-card>
      </el-col>
      <el-col :span="6" :xs="24" :sm="12" :md="6">
        <el-card shadow="never">
          <div class="stat-label">业务数据库</div>
          <div class="stat-num">{{ data.db_count }}</div>
          <div class="stat-sub">数据 {{ formatSize(storage.data_business_bytes) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6" :xs="24" :sm="12" :md="6">
        <el-card shadow="never">
          <div class="stat-label">对象存储</div>
          <div class="stat-num">{{ data.storage_ok_count }}/{{ data.storage_count }}</div>
          <div class="stat-sub">可用/已启用</div>
        </el-card>
      </el-col>
    </el-row>

    <template v-if="data.mysql_stats">
      <h3 class="section-title">MySQL 系统统计</h3>
      <el-row :gutter="16" class="mb-4">
        <el-col :span="8" :xs="24" :md="8">
          <el-card shadow="never" class="metric-card">
            <div class="metric-title">连接</div>
            <div class="metric-row">
              <span>当前连接</span>
              <strong>{{ conn.current }} / {{ conn.max_limit }}</strong>
            </div>
            <el-progress :percentage="conn.usage_percent || 0" :stroke-width="10" />
            <div class="metric-grid">
              <div><span>活跃线程</span><strong>{{ conn.running }}</strong></div>
              <div><span>历史峰值</span><strong>{{ conn.max_used }}</strong></div>
              <div><span>异常连接</span><strong>{{ conn.aborted }}</strong></div>
            </div>
          </el-card>
        </el-col>

        <el-col :span="8" :xs="24" :md="8">
          <el-card shadow="never" class="metric-card">
            <div class="metric-title">InnoDB 缓冲池</div>
            <div class="metric-row">
              <span>内存占用</span>
              <strong>{{ formatSize(memory.innodb_buffer_pool_used_bytes) }} / {{ formatSize(memory.innodb_buffer_pool_size_bytes) }}</strong>
            </div>
            <el-progress :percentage="memory.innodb_buffer_pool_usage_percent || 0" :stroke-width="10" status="success" />
            <div class="metric-grid">
              <div><span>使用率</span><strong>{{ memory.innodb_buffer_pool_usage_percent }}%</strong></div>
            </div>
          </el-card>
        </el-col>

        <el-col :span="8" :xs="24" :md="8">
          <el-card shadow="never" class="metric-card">
            <div class="metric-title">数据存储</div>
            <div class="metric-row">
              <span>业务数据</span>
              <strong>{{ formatSize(storage.data_business_bytes) }}</strong>
            </div>
            <div class="metric-row">
              <span>全部数据</span>
              <strong>{{ formatSize(storage.data_total_bytes) }}</strong>
            </div>
            <div class="metric-row">
              <span>Binlog</span>
              <strong>{{ formatSize(storage.binlog_bytes) }}</strong>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-row v-if="host.available" :gutter="16" class="mb-4">
        <el-col :span="24">
          <el-card shadow="never" class="metric-card chart-card">
            <div class="chart-toolbar">
              <span class="metric-title">系统负载趋势</span>
              <el-radio-group v-model="historyHours" size="small" @change="load()">
                <el-radio-button :value="1">近 1 小时</el-radio-button>
                <el-radio-button :value="6">近 6 小时</el-radio-button>
                <el-radio-button :value="24">近 24 小时</el-radio-button>
              </el-radio-group>
            </div>
            <div class="current-stats">
              <span>当前负载 1m：<strong>{{ host.load_1m ?? '-' }}</strong></span>
              <span>CPU：<strong>{{ host.cpu_percent ?? 0 }}%</strong></span>
            </div>
            <MetricLineChart
              title=""
              :series="loadSeries"
              y-axis-left-name="负载"
              y-axis-right-name="CPU %"
            />
          </el-card>
        </el-col>
      </el-row>

      <el-row v-if="host.available" :gutter="16" class="mb-4">
        <el-col :span="16" :xs="24" :md="16">
          <el-card shadow="never" class="metric-card chart-card">
            <div class="metric-title">内存占用趋势</div>
            <div class="current-stats">
              <span>容器：<strong>{{ host.memory_usage_percent ?? 0 }}%</strong></span>
              <span v-if="host.host_memory_usage_percent != null">
                宿主机：<strong>{{ host.host_memory_usage_percent }}%</strong>
              </span>
            </div>
            <MetricLineChart
              title=""
              :series="memorySeries"
              y-axis-left-name="使用率 %"
            />
          </el-card>
        </el-col>

        <el-col :span="8" :xs="24" :md="8">
          <el-card shadow="never" class="metric-card">
            <div class="metric-title">数据目录磁盘</div>
            <div class="metric-row">
              <span>占用</span>
              <strong>{{ formatSize(host.disk_used_bytes) }} / {{ formatSize(host.disk_total_bytes) }}</strong>
            </div>
            <el-progress :percentage="host.disk_usage_percent || 0" :stroke-width="10" :status="diskStatus" />
            <div class="metric-row">
              <span>可用</span>
              <strong>{{ formatSize(host.disk_available_bytes) }}</strong>
            </div>
            <div class="metric-row disk-detail">
              <span>容器内存</span>
              <strong>{{ formatSize(host.memory_used_bytes) }} / {{ formatSize(host.memory_total_bytes) }}</strong>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="mb-4">
        <div class="metric-title">性能指标</div>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="累计查询">{{ perf.queries_total?.toLocaleString() }}</el-descriptions-item>
          <el-descriptions-item label="慢查询">{{ perf.slow_queries?.toLocaleString() }}</el-descriptions-item>
          <el-descriptions-item label="打开表数">{{ perf.open_tables?.toLocaleString() }}</el-descriptions-item>
          <el-descriptions-item label="接收流量">{{ formatSize(perf.bytes_received) }}</el-descriptions-item>
          <el-descriptions-item label="发送流量">{{ formatSize(perf.bytes_sent) }}</el-descriptions-item>
          <el-descriptions-item label="进程数">{{ host.pids ?? '-' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
    </template>

    <el-row class="mb-4">
      <el-col>
        <el-button type="primary" size="small" :loading="triggering" @click="triggerFull">立即全量备份</el-button>
        <el-button size="small" :loading="manualLoading" @click="load()">刷新统计</el-button>
      </el-col>
    </el-row>

    <h3 class="section-title">最近备份任务</h3>
    <el-table :data="data.latest_jobs || []" stripe row-key="id">
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
      <el-table-column label="开始时间" width="170" class-name="time-col">
        <template #default="{ row }">
          <span class="time-cell">{{ formatDateTime(row.started_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="耗时" width="100">
        <template #default="{ row }">{{ row.duration_seconds ? row.duration_seconds + 's' : '-' }}</template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef } from 'vue'
import { ElMessage } from 'element-plus'
import { dashboardApi, backupApi } from '@/api'
import { formatDateTime } from '@/utils/datetime'
import MetricLineChart from '@/components/MetricLineChart.vue'

const pageLoading = ref(false)
const manualLoading = ref(false)
const triggering = ref(false)
const historyHours = ref(6)
const loadSeries = shallowRef([])
const memorySeries = shallowRef([])
let refreshTimer = null
let refreshing = false

const data = reactive({
  mysql_status: {},
  mysql_stats: null,
  host_stats: null,
  collected_at: null,
  mysql_error: '',
  db_count: 0,
  storage_count: 0,
  storage_ok_count: 0,
  latest_jobs: [],
})

const conn = computed(() => data.mysql_stats?.connections || {})
const memory = computed(() => data.mysql_stats?.memory || {})
const storage = computed(() => data.mysql_stats?.storage || {})
const perf = computed(() => data.mysql_stats?.performance || {})
const host = computed(() => data.host_stats || {})

const diskStatus = computed(() => {
  const p = host.value.disk_usage_percent || 0
  if (p >= 90) return 'exception'
  if (p >= 75) return 'warning'
  return undefined
})

function downsample(points, maxPoints = 480) {
  if (!points || points.length <= maxPoints) return points || []
  const last = points.length - 1
  const step = last / (maxPoints - 1)
  const sampled = []
  let prev = -1
  for (let i = 0; i < maxPoints; i += 1) {
    const idx = Math.min(last, Math.round(i * step))
    if (idx !== prev) sampled.push(points[idx])
    prev = idx
  }
  if (sampled[sampled.length - 1] !== points[last]) sampled.push(points[last])
  return sampled
}

function toPairs(points, key) {
  const out = []
  for (let i = 0; i < points.length; i += 1) {
    const value = points[i][key]
    if (value != null) out.push([points[i].ts * 1000, value])
  }
  return out
}

function rebuildSeries(points) {
  const sampled = downsample(points)
  loadSeries.value = [
    { name: '负载 (1m)', data: toPairs(sampled, 'load_1m'), yAxisIndex: 0, area: true },
    { name: 'CPU %', data: toPairs(sampled, 'cpu_percent'), yAxisIndex: 1 },
  ]
  const memory = [
    { name: '容器内存 %', data: toPairs(sampled, 'memory_usage_percent'), area: true },
  ]
  if (sampled.some((p) => p.host_memory_usage_percent != null)) {
    memory.push({ name: '宿主机内存 %', data: toPairs(sampled, 'host_memory_usage_percent') })
  }
  memorySeries.value = memory
}

function formatSize(bytes) {
  if (bytes == null || bytes === 0) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return (bytes / 1024 ** 3).toFixed(2) + ' GB'
}

function statusType(status) {
  return { success: 'success', failed: 'danger', partial: 'warning', running: 'primary' }[status] || 'info'
}

async function load({ silent = false } = {}) {
  if (silent) {
    if (refreshing) return
    refreshing = true
  } else if (!data.collected_at) {
    pageLoading.value = true
  } else {
    manualLoading.value = true
  }
  try {
    const { data: res } = await dashboardApi.get(historyHours.value)
    rebuildSeries(res.host_metrics_history?.points || [])
    const rest = { ...res }
    delete rest.host_metrics_history
    Object.assign(data, rest)
  } finally {
    pageLoading.value = false
    manualLoading.value = false
    refreshing = false
  }
}

async function triggerFull() {
  triggering.value = true
  try {
    await backupApi.trigger('full')
    ElMessage.success('已提交全量备份任务')
    await load()
  } finally {
    triggering.value = false
  }
}

onMounted(() => {
  load()
  refreshTimer = setInterval(() => load({ silent: true }), 3000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.page-title { margin: 0 0 20px; }
.section-title { margin: 0 0 12px; font-size: 16px; font-weight: 600; }
.mb-4 { margin-bottom: 20px; }
.stat-label { color: #909399; font-size: 13px; margin-bottom: 8px; }
.stat-num { font-size: 28px; font-weight: 600; }
.stat-value { font-size: 18px; display: flex; align-items: center; gap: 4px; }
.stat-value.ok { color: #67c23a; }
.stat-value.err { color: #f56c6c; }
.stat-sub { color: #909399; font-size: 12px; margin-top: 4px; }
.metric-card { min-height: 180px; }
.metric-title { font-weight: 600; margin-bottom: 12px; color: #303133; }
.metric-row { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 13px; color: #606266; }
.metric-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 12px; font-size: 12px; }
.metric-grid span { display: block; color: #909399; margin-bottom: 2px; }
.chart-card { min-height: auto; }
.chart-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.current-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 8px;
  font-size: 13px;
  color: #606266;
}
.current-stats strong { color: #303133; }
.disk-detail { margin-top: 16px; }
.page-hint { margin: -8px 0 16px; font-size: 13px; color: #909399; }
:deep(.el-progress-bar__inner) { transition: none; }
:deep(.time-col .cell) { white-space: nowrap; }
</style>
