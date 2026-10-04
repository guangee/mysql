<template>
  <div v-loading="loading">
    <div class="page-header">
      <div>
        <h2 class="page-title">参数配置</h2>
        <div class="page-hint">
          运行值 / 建议值（按检测到的可用内存） / 推荐值（按所选内存档位）。支持按 1G~128G 一键套用常见优化。
        </div>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button @click="resetDraft" :disabled="!changedCount">重置未保存修改</el-button>
        <el-button
          type="primary"
          :loading="saving === 'all'"
          :disabled="!changedCount"
          @click="saveAll"
        >
          保存全部修改 ({{ changedCount }})
        </el-button>
      </div>
    </div>

    <el-card shadow="never" class="mb-3 service-card">
      <div class="service-head">
        <div>
          <div class="service-title">MySQL 服务控制</div>
          <div class="service-sub">
            容器 <code>{{ service.container || '-' }}</code>
            · 状态
            <el-tag size="small" :type="serviceTagType" class="ml-1">{{ serviceStatusLabel }}</el-tag>
            <span v-if="service.ready" class="ready-ok"> · 已就绪</span>
            <span v-else-if="service.running" class="ready-wait"> · 启动中/未就绪</span>
          </div>
        </div>
        <div class="service-actions">
          <el-button size="small" :loading="serviceLoading && !serviceAction" @click="refreshService">刷新状态</el-button>
          <el-button
            type="success"
            size="small"
            :loading="serviceAction === 'start'"
            :disabled="service.running || !!serviceAction"
            @click="controlService('start')"
          >
            启动
          </el-button>
          <el-button
            type="warning"
            size="small"
            :loading="serviceAction === 'stop'"
            :disabled="!service.running || !!serviceAction"
            @click="controlService('stop')"
          >
            关闭
          </el-button>
          <el-button
            type="danger"
            size="small"
            :loading="serviceAction === 'restart'"
            :disabled="!service.exists || !!serviceAction"
            @click="controlService('restart')"
          >
            重启
          </el-button>
        </div>
      </div>
      <div v-if="service.error" class="service-error">{{ service.error }}</div>
      <div class="service-meta">
        <span v-if="service.started_at">启动时间：{{ formatServiceTime(service.started_at) }}</span>
        <span v-if="service.image">镜像：{{ service.image }}</span>
      </div>
    </el-card>

    <el-alert
      v-if="overview.restart_required"
      type="warning"
      show-icon
      :closable="false"
      class="mb-3"
    >
      <template #default>
        <div class="restart-alert-body">
          <span>存在已写入配置但尚未生效的参数，需重启 MySQL 容器后才会与运行值一致。</span>
          <el-button
            type="warning"
            size="small"
            :loading="serviceAction === 'restart'"
            :disabled="!!serviceAction"
            @click="controlService('restart')"
          >
            立即重启
          </el-button>
        </div>
      </template>
    </el-alert>
    <el-alert
      v-if="error"
      type="error"
      show-icon
      :closable="false"
      class="mb-3"
      :title="error"
    />

    <el-card shadow="never" class="mb-3 preset-card">
      <div class="preset-head">
        <div>
          <div class="preset-title">按可用内存一键优化</div>
          <div class="preset-sub">
            检测到可用内存
            <strong>{{ detectedMemoryLabel }}</strong>
            <span v-if="overview.memory?.suggested_gb">（建议档位 {{ overview.memory.suggested_gb }}G）</span>
            · 当前推荐档位 <strong>{{ selectedMemoryGb }}G</strong>
          </div>
        </div>
        <div class="preset-actions">
          <el-button size="small" @click="fillSuggested">填入建议值</el-button>
          <el-button size="small" @click="fillRecommended">填入推荐值</el-button>
          <el-button
            type="primary"
            size="small"
            :loading="saving === 'preset'"
            @click="applyPreset"
          >
            一键应用 {{ selectedMemoryGb }}G 优化
          </el-button>
        </div>
      </div>
      <div class="preset-btns">
        <el-button
          v-for="preset in overview.memory?.presets || []"
          :key="preset.gb"
          size="default"
          :type="selectedMemoryGb === preset.gb ? 'primary' : 'default'"
          :plain="selectedMemoryGb !== preset.gb"
          @click="selectMemoryGb(preset.gb)"
        >
          {{ preset.label }}
        </el-button>
      </div>
      <div v-if="activePreset" class="preset-summary">{{ activePreset.summary }}</div>
    </el-card>

    <el-row :gutter="12" class="mb-3">
      <el-col :span="4" :xs="12" :sm="8" :md="4" v-for="card in summaryCards" :key="card.label">
        <el-card shadow="never" class="summary-card">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value">{{ card.value }}</div>
          <div class="stat-sub">{{ card.sub }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="mb-3">
      <div class="toolbar">
        <el-input
          v-model="keyword"
          clearable
          placeholder="搜索参数名 / 中文名 / 描述"
          style="width: 280px"
        />
        <el-radio-group v-model="statusFilter" size="small">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="changed">未保存修改</el-radio-button>
          <el-radio-button value="diff_suggest">偏离建议</el-radio-button>
          <el-radio-button value="diff_recommend">偏离推荐</el-radio-button>
          <el-radio-button value="dirty">配置与运行不一致</el-radio-button>
          <el-radio-button value="restart">需重启</el-radio-button>
        </el-radio-group>
        <el-select v-model="riskFilter" clearable placeholder="风险等级" style="width: 120px">
          <el-option label="低风险" value="low" />
          <el-option label="中风险" value="medium" />
          <el-option label="高风险" value="high" />
        </el-select>
      </div>
    </el-card>

    <el-row :gutter="16">
      <el-col :span="5" :xs="24">
        <el-card shadow="never" class="side-card">
          <div
            class="cat-item"
            :class="{ active: activeCategory === 'all' }"
            @click="activeCategory = 'all'"
          >
            <div class="cat-title">全部参数</div>
            <div class="cat-count">{{ overview.items?.length || 0 }}</div>
          </div>
          <div
            v-for="cat in overview.categories || []"
            :key="cat.key"
            class="cat-item"
            :class="{ active: activeCategory === cat.key }"
            @click="activeCategory = cat.key"
          >
            <div class="cat-title">{{ cat.label }}</div>
            <div class="cat-count">{{ cat.count }}</div>
            <div class="cat-summary">{{ cat.summary }}</div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="19" :xs="24">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>{{ currentCategoryLabel }} · {{ filteredItems.length }} 项</span>
              <el-button
                v-if="activeCategory !== 'all'"
                type="primary"
                size="small"
                :loading="saving === activeCategory"
                :disabled="!categoryChangedCount"
                @click="saveCategory"
              >
                保存本分类 ({{ categoryChangedCount }})
              </el-button>
            </div>
          </template>

          <el-table :data="filteredItems" stripe border row-key="key" max-height="680">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="expand-box">
                  <div><strong>说明：</strong>{{ row.description }}</div>
                  <div v-if="row.recommend" class="mt-1"><strong>调优提示：</strong>{{ row.recommend }}</div>
                  <div class="mt-1 mono">
                    范围：
                    <template v-if="row.value_type === 'enum'">{{ (row.enum_options || []).join(' / ') }}</template>
                    <template v-else>{{ displayBound(row) }}</template>
                  </div>
                  <div class="value-compare mt-1">
                    运行 {{ displayValue(row, row.value) }}
                    · 建议 {{ displayValue(row, row.suggested_value) }}
                    · 推荐 {{ displayValue(row, displayRecommended(row)) }}
                  </div>
                  <div v-if="row.usage" class="usage-block">
                    <div class="usage-text">
                      <template v-if="row.usage.used_bytes != null">
                        {{ row.usage.label }}：{{ formatSize(row.usage.used_bytes) }}
                        <span v-if="row.usage.percent != null">（{{ row.usage.percent }}%）</span>
                      </template>
                      <template v-else>
                        {{ row.usage.label }}：{{ row.usage.current ?? '-' }}
                        <span v-if="row.usage.created != null"> · 历史创建线程 {{ row.usage.created }}</span>
                        <span v-if="row.usage.percent != null">（{{ row.usage.percent }}%）</span>
                      </template>
                    </div>
                    <el-progress
                      v-if="row.usage.percent != null"
                      :percentage="Math.min(Number(row.usage.percent) || 0, 100)"
                      :stroke-width="8"
                      :status="row.usage.percent >= 85 ? 'exception' : undefined"
                    />
                  </div>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="参数名" min-width="180" fixed>
              <template #default="{ row }">
                <div class="param-name"><code>{{ row.key }}</code></div>
                <div class="param-label">{{ row.label }}</div>
              </template>
            </el-table-column>

            <el-table-column label="运行值" width="120">
              <template #default="{ row }">
                <span :class="{ changed: isChanged(row) }">{{ displayValue(row, row.value) }}</span>
              </template>
            </el-table-column>

            <el-table-column label="建议值" width="120">
              <template #default="{ row }">
                <span
                  class="clickable"
                  :class="{ highlight: !sameValue(row, row.value, row.suggested_value) }"
                  title="点击填入编辑值"
                  @click="applyOneValue(row, row.suggested_value)"
                >
                  {{ displayValue(row, row.suggested_value) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column :label="`推荐值(${selectedMemoryGb}G)`" width="130">
              <template #default="{ row }">
                <span
                  class="clickable"
                  :class="{ highlight: !sameValue(row, row.value, displayRecommended(row)) }"
                  title="点击填入编辑值"
                  @click="applyOneValue(row, displayRecommended(row))"
                >
                  {{ displayValue(row, displayRecommended(row)) }}
                </span>
              </template>
            </el-table-column>

            <el-table-column label="编辑值" width="200">
              <template #default="{ row }">
                <div class="edit-cell">
                  <template v-if="row.value_type === 'enum'">
                    <el-select v-model="draft[row.key]" size="small" style="width: 130px">
                      <el-option v-for="opt in row.enum_options" :key="opt" :label="opt" :value="opt" />
                    </el-select>
                  </template>
                  <template v-else-if="row.unit === 'bytes'">
                    <el-input-number
                      v-model="draft[row.key]"
                      size="small"
                      :min="bytesMinMB(row)"
                      :max="bytesMaxMB(row)"
                      :step="bytesStepMB(row)"
                      controls-position="right"
                    />
                    <span class="unit">MB</span>
                  </template>
                  <template v-else>
                    <el-input-number
                      v-model="draft[row.key]"
                      size="small"
                      :min="row.min"
                      :max="row.max == null ? undefined : row.max"
                      :step="row.step || 1"
                      :precision="row.value_type === 'float' ? 1 : 0"
                      controls-position="right"
                    />
                    <span v-if="row.unit === 'seconds'" class="unit">秒</span>
                  </template>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="配置文件" width="110">
              <template #default="{ row }">
                <span v-if="row.persisted_value == null" class="muted">未落盘</span>
                <span v-else :class="{ warn: row.dirty }">{{ displayValue(row, row.persisted_value) }}</span>
              </template>
            </el-table-column>

            <el-table-column label="生效" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.dynamic" size="small" type="success">即时</el-tag>
                <el-tag v-else size="small" type="warning">需重启</el-tag>
              </template>
            </el-table-column>

            <el-table-column label="风险" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="riskType(row.risk)">{{ riskLabel(row.risk) }}</el-tag>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link :disabled="!isChanged(row)" @click="resetOne(row)">还原</el-button>
                <el-button
                  size="small"
                  link
                  type="primary"
                  :disabled="!isChanged(row)"
                  :loading="saving === row.key"
                  @click="saveOne(row)"
                >
                  保存
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <div v-if="overview.config_file" class="config-hint">
          持久化文件：<code>{{ overview.config_file }}</code>
          <span v-if="overview.chunk_size_bytes"> · 缓冲池步长 {{ formatSize(overview.chunk_size_bytes) }}</span>
          <span v-if="overview.instance?.version"> · {{ overview.instance.version }}</span>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { mysqlApi } from '@/api'

const loading = ref(false)
const saving = ref('')
const error = ref('')
const keyword = ref('')
const statusFilter = ref('all')
const riskFilter = ref('')
const activeCategory = ref('all')
const selectedMemoryGb = ref(8)
const serviceLoading = ref(false)
const serviceAction = ref('')
const service = reactive({
  container: '',
  exists: false,
  running: false,
  ready: false,
  status: 'unknown',
  error: '',
  started_at: '',
  image: '',
})
const overview = reactive({
  groups: [],
  items: [],
  categories: [],
  summary: {},
  instance: {},
  memory: { presets: [] },
  restart_required: false,
  config_file: '',
  chunk_size_bytes: 0,
})
const draft = reactive({})
const original = reactive({})

const serviceStatusLabel = computed(() => {
  if (!service.exists) return '不存在'
  if (service.restarting) return '重启中'
  if (service.paused) return '已暂停'
  if (service.running && service.ready) return '运行中'
  if (service.running) return '运行中(未就绪)'
  return service.status || '已停止'
})
const serviceTagType = computed(() => {
  if (service.ready) return 'success'
  if (service.running) return 'warning'
  if (!service.exists) return 'danger'
  return 'info'
})

const activePreset = computed(() =>
  (overview.memory?.presets || []).find((p) => p.gb === selectedMemoryGb.value),
)

const detectedMemoryLabel = computed(() => {
  const bytes = overview.memory?.detected_bytes
  if (!bytes) return '未知'
  return formatSize(bytes)
})

const summaryCards = computed(() => {
  const s = overview.summary || {}
  return [
    { label: '可管参数', value: s.total ?? 0, sub: `即时生效 ${s.dynamic ?? 0}` },
    { label: '未保存修改', value: changedCount.value, sub: '当前草稿相对运行值' },
    { label: '建议档位', value: `${overview.memory?.suggested_gb || '-'}G`, sub: detectedMemoryLabel.value },
    { label: '推荐档位', value: `${selectedMemoryGb.value}G`, sub: activePreset.value?.summary?.split('：')[0] || '' },
    {
      label: '当前连接',
      value: `${s.connections_current ?? '-'}`,
      sub: `活跃 ${s.connections_running ?? '-'} · 峰值 ${s.connections_max_used ?? '-'}`,
    },
    { label: '待重启生效', value: s.needs_restart ?? 0, sub: overview.restart_required ? '需要重启容器' : '暂无' },
  ]
})

const currentCategoryLabel = computed(() => {
  if (activeCategory.value === 'all') return '全部参数'
  const cat = (overview.categories || []).find((c) => c.key === activeCategory.value)
  return cat?.label || '参数'
})

const filteredItems = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return (overview.items || []).filter((item) => {
    if (activeCategory.value !== 'all' && item.category !== activeCategory.value) return false
    if (riskFilter.value && item.risk !== riskFilter.value) return false
    if (statusFilter.value === 'changed' && !isChanged(item)) return false
    if (statusFilter.value === 'diff_suggest' && sameValue(item, item.value, item.suggested_value)) return false
    if (statusFilter.value === 'diff_recommend' && sameValue(item, item.value, displayRecommended(item))) return false
    if (statusFilter.value === 'dirty' && !item.dirty) return false
    if (statusFilter.value === 'restart' && item.dynamic) return false
    if (!kw) return true
    return (
      item.key.toLowerCase().includes(kw) ||
      item.label.toLowerCase().includes(kw) ||
      (item.description || '').toLowerCase().includes(kw)
    )
  })
})

const changedCount = computed(() => (overview.items || []).filter((item) => isChanged(item)).length)
const categoryChangedCount = computed(() =>
  (overview.items || []).filter((item) => item.category === activeCategory.value && isChanged(item)).length,
)

function formatSize(bytes) {
  const n = Number(bytes || 0)
  if (!n) return '0 B'
  if (n < 1024 ** 2) return (n / 1024).toFixed(1) + ' KB'
  if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + ' MB'
  return (n / 1024 ** 3).toFixed(2) + ' GB'
}

function bytesStepMB(item) {
  return Math.max((item.step || overview.chunk_size_bytes || 134217728) / 1024 / 1024, 1)
}
function bytesMinMB(item) {
  return Math.ceil((item.min || 0) / 1024 / 1024)
}
function bytesMaxMB(item) {
  return item.max ? Math.floor(item.max / 1024 / 1024) : 1024 * 256
}

function displayValue(item, value) {
  if (value == null || value === '') return '-'
  if (item.unit === 'bytes') return formatSize(value)
  if (item.unit === 'seconds' && item.value_type !== 'float') return `${value} 秒`
  if (item.unit === 'seconds' && item.value_type === 'float') return `${value} 秒`
  return String(value)
}

function displayBound(item) {
  if (item.unit === 'bytes') {
    const min = item.min != null ? formatSize(item.min) : '-'
    const max = item.max != null ? formatSize(item.max) : '不限'
    return `${min} ~ ${max}`
  }
  return `${item.min ?? '-'} ~ ${item.max ?? '不限'}`
}

function displayRecommended(item) {
  const preset = activePreset.value
  if (preset?.settings && preset.settings[item.key] != null) return preset.settings[item.key]
  return item.recommended_value
}

function sameValue(item, a, b) {
  if (a == null || b == null) return a == b
  if (item.value_type === 'enum') return String(a) === String(b)
  if (item.value_type === 'float') return Math.abs(Number(a) - Number(b)) < 1e-9
  return Number(a) === Number(b) || String(a) === String(b)
}

function riskType(risk) {
  return { low: 'success', medium: 'warning', high: 'danger' }[risk] || 'info'
}
function riskLabel(risk) {
  return { low: '低', medium: '中', high: '高' }[risk] || risk
}

function toDraftValue(item, value = item.value) {
  if (item.value_type === 'enum') return String(value)
  if (item.unit === 'bytes') return Math.round(Number(value) / 1024 / 1024)
  if (item.value_type === 'float') return Number(value)
  return Number(value)
}

function toApiValue(item, draftVal) {
  if (item.value_type === 'enum') return String(draftVal)
  if (item.unit === 'bytes') return Math.round(Number(draftVal) * 1024 * 1024)
  if (item.value_type === 'float') return Number(draftVal)
  return Math.round(Number(draftVal))
}

function syncDraft(data) {
  Object.keys(draft).forEach((k) => delete draft[k])
  Object.keys(original).forEach((k) => delete original[k])
  for (const item of data.items || []) {
    const v = toDraftValue(item)
    draft[item.key] = v
    original[item.key] = v
  }
}

function isChanged(item) {
  return draft[item.key] !== original[item.key]
}

function resetOne(item) {
  draft[item.key] = original[item.key]
}

function resetDraft() {
  for (const item of overview.items || []) {
    draft[item.key] = original[item.key]
  }
}

function applyOneValue(item, value) {
  if (value == null) return
  draft[item.key] = toDraftValue(item, value)
}

function fillFromSettings(settings) {
  if (!settings) return
  for (const item of overview.items || []) {
    if (settings[item.key] != null) draft[item.key] = toDraftValue(item, settings[item.key])
  }
}

function fillSuggested() {
  for (const item of overview.items || []) {
    if (item.suggested_value != null) draft[item.key] = toDraftValue(item, item.suggested_value)
  }
  ElMessage.success(`已按建议档位 ${overview.memory?.suggested_gb || '-'}G 填入编辑值，请检查后保存`)
}

function fillRecommended() {
  fillFromSettings(activePreset.value?.settings)
  ElMessage.success(`已按推荐档位 ${selectedMemoryGb.value}G 填入编辑值，请检查后保存`)
}

async function selectMemoryGb(gb) {
  selectedMemoryGb.value = gb
  const preset = (overview.memory?.presets || []).find((p) => p.gb === gb)
  if (preset) {
    for (const item of overview.items || []) {
      if (preset.settings[item.key] != null) item.recommended_value = preset.settings[item.key]
    }
    if (overview.memory) overview.memory.recommended_gb = gb
  }
  fillFromSettings(preset?.settings)
}

function collectChanges(items) {
  const settings = {}
  for (const item of items) {
    if (isChanged(item)) settings[item.key] = toApiValue(item, draft[item.key])
  }
  return settings
}

async function refreshService() {
  serviceLoading.value = true
  try {
    const { data } = await mysqlApi.serviceStatus()
    Object.assign(service, data)
  } catch (e) {
    // 全局拦截器已提示
  } finally {
    serviceLoading.value = false
  }
}

function formatServiceTime(iso) {
  if (!iso || iso.startsWith('0001')) return '-'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

async function controlService(action) {
  const labels = { start: '启动', stop: '关闭', restart: '重启' }
  const label = labels[action] || action
  try {
    await ElMessageBox.confirm(
      action === 'stop'
        ? '关闭后业务将无法连接数据库，确认关闭 MySQL？'
        : action === 'restart'
          ? '重启期间业务会短暂中断，用于让需重启参数生效。确认重启 MySQL？'
          : '确认启动 MySQL 容器？',
      `确认${label} MySQL`,
      {
        type: action === 'stop' ? 'error' : 'warning',
        confirmButtonText: `确认${label}`,
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  serviceAction.value = action
  try {
    const { data } = await mysqlApi.controlService({ action, wait_ready: action !== 'stop' })
    Object.assign(service, data)
    ElMessage.success(data.message || `${label}成功`)
    if (action !== 'stop' && data.ready) {
      await load()
    }
  } catch (e) {
    await refreshService()
  } finally {
    serviceAction.value = ''
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = selectedMemoryGb.value ? { recommended_memory_gb: selectedMemoryGb.value } : {}
    const { data } = await mysqlApi.settings(params)
    Object.assign(overview, data)
    if (!overview.memory) overview.memory = { presets: [] }
    if (data.memory?.suggested_gb && !selectedMemoryGb.value) {
      selectedMemoryGb.value = data.memory.suggested_gb
    } else if (data.memory?.recommended_gb) {
      selectedMemoryGb.value = data.memory.recommended_gb
    } else if (data.memory?.suggested_gb && overview.items?.length && !changedCount.value) {
      // keep user selection; first load defaults to suggested
    }
    if (!selectedMemoryGb.value && data.memory?.suggested_gb) {
      selectedMemoryGb.value = data.memory.suggested_gb
    }
    // First mount: prefer suggested tier
    if (!data.memory?.recommended_gb && data.memory?.suggested_gb && selectedMemoryGb.value === 8) {
      const first = !overview._loaded
      if (first) selectedMemoryGb.value = data.memory.suggested_gb
    }
    overview._loaded = true
    syncDraft(overview)
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function applySettings(settings, savingKey) {
  if (!Object.keys(settings).length) return
  const restartKeys = (overview.items || [])
    .filter((item) => !item.dynamic && settings[item.key] != null)
    .map((item) => item.key)
  if (restartKeys.length) {
    await ElMessageBox.confirm(
      `以下参数保存后需重启 MySQL 才生效：\n${restartKeys.join(', ')}\n\n是否继续？`,
      '确认保存',
      { type: 'warning', confirmButtonText: '继续保存', cancelButtonText: '取消' },
    )
  }
  const highRisk = (overview.items || []).filter((item) => item.risk === 'high' && settings[item.key] != null)
  if (highRisk.length) {
    await ElMessageBox.confirm(
      `包含高风险参数：${highRisk.map((i) => i.key).join(', ')}。错误配置可能影响稳定性，确认继续？`,
      '高风险确认',
      { type: 'error', confirmButtonText: '确认修改', cancelButtonText: '取消' },
    )
  }

  saving.value = savingKey
  try {
    const { data } = await mysqlApi.updateSettings({
      settings,
      recommended_memory_gb: selectedMemoryGb.value,
    })
    Object.assign(overview, data.overview || {})
    syncDraft(data.overview || overview)
    if (data.restart_required) ElMessage.success('已保存；部分参数需重启 MySQL 后生效')
    else ElMessage.success('参数已保存并生效')
  } catch (e) {
    if (e !== 'cancel' && e?.toString?.() !== 'cancel') {
      ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
    }
  } finally {
    saving.value = ''
  }
}

async function applyPreset() {
  const gb = selectedMemoryGb.value
  const preset = activePreset.value
  if (!preset) return
  try {
    await ElMessageBox.confirm(
      `将按 ${gb}G 可用内存一键应用常见优化（缓冲池、连接数、表缓存、IO 等共 ${Object.keys(preset.settings).length} 项）。\n\n${preset.summary}\n\n部分参数可能需重启后生效，是否继续？`,
      `确认应用 ${gb}G 优化`,
      { type: 'warning', confirmButtonText: '一键应用', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  saving.value = 'preset'
  try {
    const { data } = await mysqlApi.updateSettings({ preset_memory_gb: gb })
    Object.assign(overview, data.overview || {})
    selectedMemoryGb.value = data.preset_memory_gb || gb
    syncDraft(data.overview || overview)
    if (data.restart_required) {
      ElMessage.success(`${gb}G 优化已写入；部分参数需重启 MySQL 后生效`)
    } else {
      ElMessage.success(`${gb}G 优化已应用`)
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '应用失败')
  } finally {
    saving.value = ''
  }
}

async function saveOne(item) {
  await applySettings(collectChanges([item]), item.key)
}
async function saveCategory() {
  const items = (overview.items || []).filter((item) => item.category === activeCategory.value)
  await applySettings(collectChanges(items), activeCategory.value)
}
async function saveAll() {
  await applySettings(collectChanges(overview.items || []), 'all')
}

onMounted(async () => {
  await Promise.all([refreshService(), (async () => {
    loading.value = true
    error.value = ''
    try {
      const { data } = await mysqlApi.settings()
      Object.assign(overview, data)
      selectedMemoryGb.value = data.memory?.suggested_gb || data.memory?.recommended_gb || 8
      if (!overview.memory) overview.memory = { presets: [] }
      overview._loaded = true
      syncDraft(overview)
    } catch (e) {
      error.value = e.response?.data?.detail || e.message || '加载失败'
    } finally {
      loading.value = false
    }
  })()])
})
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 16px;
}
.page-title { margin: 0 0 4px; }
.page-hint { color: #909399; font-size: 13px; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.mb-3 { margin-bottom: 16px; }
.mt-1 { margin-top: 6px; }
.ml-1 { margin-left: 6px; }
.service-card { border: 1px solid #e4e7ed; }
.service-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  flex-wrap: wrap;
}
.service-title { font-size: 16px; font-weight: 600; color: #303133; }
.service-sub { margin-top: 6px; color: #606266; font-size: 13px; }
.service-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.service-error { margin-top: 8px; color: #f56c6c; font-size: 13px; }
.service-meta {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.ready-ok { color: #67c23a; }
.ready-wait { color: #e6a23c; }
.restart-alert-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.preset-card { border: 1px solid #e4e7ed; }
.preset-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.preset-title { font-size: 16px; font-weight: 600; color: #303133; }
.preset-sub { margin-top: 4px; color: #909399; font-size: 13px; }
.preset-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.preset-btns { display: flex; flex-wrap: wrap; gap: 8px; }
.preset-summary { margin-top: 10px; color: #606266; font-size: 13px; }
.summary-card { min-height: 92px; }
.stat-label { color: #909399; font-size: 12px; margin-bottom: 6px; }
.stat-value { font-size: 22px; font-weight: 600; line-height: 1.2; }
.stat-sub { margin-top: 4px; color: #909399; font-size: 12px; }
.toolbar {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
}
.side-card { position: sticky; top: 12px; }
.cat-item {
  padding: 10px 8px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 6px;
}
.cat-item:hover { background: #f5f7fa; }
.cat-item.active { background: #ecf5ff; }
.cat-title { font-weight: 600; color: #303133; }
.cat-count { float: right; color: #909399; font-size: 12px; }
.cat-summary { clear: both; margin-top: 4px; color: #909399; font-size: 12px; line-height: 1.4; }
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.param-name code { font-size: 12px; }
.param-label { margin-top: 4px; color: #606266; font-size: 12px; }
.edit-cell { display: flex; align-items: center; justify-content: flex-end; gap: 6px; }
.unit { color: #909399; font-size: 12px; }
.muted { color: #909399; }
.warn { color: #e6a23c; }
.changed { color: #f56c6c; font-weight: 600; }
.highlight { color: #e6a23c; }
.clickable { cursor: pointer; }
.clickable:hover { text-decoration: underline; }
.expand-box { padding: 4px 12px 12px 48px; color: #606266; font-size: 13px; line-height: 1.6; }
.value-compare { color: #909399; font-size: 12px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.usage-block { margin-top: 10px; max-width: 420px; }
.usage-text { margin-bottom: 6px; font-size: 12px; }
.config-hint { margin-top: 12px; color: #909399; font-size: 13px; }
</style>
