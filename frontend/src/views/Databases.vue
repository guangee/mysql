<template>
  <div v-loading="loading">
    <div class="page-header">
      <h2 class="page-title">业务数据库</h2>
      <el-button type="primary" @click="openCreate">新建数据库</el-button>
    </div>

    <el-row v-if="summary" :gutter="12" class="summary-row">
      <el-col :span="6" :xs="12" :sm="8" :md="6">
        <el-card shadow="never" class="summary-card">
          <div class="summary-label">数据库</div>
          <div class="summary-value">{{ summary.database_count }}</div>
          <div class="summary-sub">业务库总量 {{ summary.total_size_display }}</div>
        </el-card>
      </el-col>
      <el-col :span="6" :xs="12" :sm="8" :md="6">
        <el-card shadow="never" class="summary-card">
          <div class="summary-label">库级连接</div>
          <div class="summary-value">{{ summary.total_connections }}</div>
          <div class="summary-sub">
            实例 {{ summary.mysql_connections?.current ?? '-' }}/{{ summary.mysql_connections?.max_limit ?? '-' }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="6" :xs="12" :sm="8" :md="6">
        <el-card shadow="never" class="summary-card">
          <div class="summary-label">InnoDB 缓冲池</div>
          <div class="summary-value">{{ formatSize(summary.mysql_memory?.innodb_buffer_pool_used_bytes) }}</div>
          <el-progress
            :percentage="summary.mysql_memory?.innodb_buffer_pool_usage_percent || 0"
            :stroke-width="6"
            class="mini-progress"
          />
        </el-card>
      </el-col>
      <el-col :span="6" :xs="12" :sm="8" :md="6">
        <el-card shadow="never" class="summary-card">
          <div class="summary-label">系统负载 / CPU</div>
          <div class="summary-value">{{ host.load_1m ?? '-' }} / {{ host.cpu_percent ?? '-' }}%</div>
          <div class="summary-sub">
            内存 {{ formatSize(host.host_memory_used_bytes) }}/{{ formatSize(host.host_memory_total_bytes) }}
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-table :data="items" stripe border class="db-table">
      <el-table-column prop="name" label="库名" min-width="130" fixed>
        <template #default="{ row }">
          <el-link type="primary" @click="router.push(`/databases/${row.name}`)">
            <code>{{ row.name }}</code>
          </el-link>
        </template>
      </el-table-column>

      <el-table-column label="存储引擎" min-width="150">
        <template #default="{ row }">
          <el-tag size="small" type="primary">{{ row.primary_engine }}</el-tag>
          <el-tooltip v-if="row.engines?.length > 1" placement="top">
            <template #content>
              <div v-for="e in row.engines" :key="e.engine">
                {{ e.engine }}：{{ e.table_count }} 表 / {{ formatSize(e.size_bytes) }}
              </div>
            </template>
            <el-tag size="small" type="info" class="ml-1">+{{ row.engines.length - 1 }}</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>

      <el-table-column prop="table_count" label="表数" width="70" align="right" />
      <el-table-column label="行数(约)" width="100" align="right">
        <template #default="{ row }">{{ formatRows(row.row_count) }}</template>
      </el-table-column>

      <el-table-column label="数据大小" width="100" align="right">
        <template #default="{ row }">{{ row.data_size_display }}</template>
      </el-table-column>
      <el-table-column label="索引大小" width="100" align="right">
        <template #default="{ row }">{{ row.index_size_display }}</template>
      </el-table-column>
      <el-table-column label="总大小" width="100" align="right">
        <template #default="{ row }"><strong>{{ row.size_display }}</strong></template>
      </el-table-column>

      <el-table-column label="连接数" width="80" align="right">
        <template #default="{ row }">
          <el-tag :type="row.connection_count > 0 ? 'warning' : 'info'" size="small">
            {{ row.connection_count }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="charset" label="字符集" width="100" />
      <el-table-column prop="collation" label="排序规则" min-width="150" show-overflow-tooltip />

      <el-table-column label="关联用户" min-width="180">
        <template #default="{ row }">
          <el-tag v-for="u in row.users" :key="u" size="small" class="mr-1">{{ u }}</el-tag>
          <span v-if="!row.users?.length" class="muted">-</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" link @click="router.push(`/databases/${row.name}`)">
            进入
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVisible" title="新建数据库" width="480px" destroy-on-close>
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="库名" required>
          <el-input v-model="createForm.name" placeholder="字母、数字、下划线" />
        </el-form-item>
        <el-form-item label="字符集">
          <el-select v-model="createForm.charset">
            <el-option label="utf8mb4（推荐）" value="utf8mb4" />
            <el-option label="utf8" value="utf8" />
            <el-option label="latin1" value="latin1" />
          </el-select>
        </el-form-item>
        <el-form-item label="排序规则">
          <el-input v-model="createForm.collation" placeholder="留空使用默认" />
        </el-form-item>
        <el-divider content-position="left">授权（可选）</el-divider>
        <el-form-item label="授权用户">
          <el-select v-model="createForm.grant_target" clearable filterable placeholder="选择已有用户">
            <el-option
              v-for="u in businessUsers"
              :key="`${u.user}@${u.host}`"
              :label="`${u.user}@${u.host}`"
              :value="`${u.user}@${u.host}`"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="createForm.grant_target" label="权限">
          <el-select v-model="createForm.privilege_level">
            <el-option label="全部权限" value="all" />
            <el-option label="读写" value="read_write" />
            <el-option label="只读" value="read_only" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { accountApi, databaseApi } from '@/api'

const router = useRouter()

const loading = ref(false)
const creating = ref(false)
const createVisible = ref(false)
const items = ref([])
const summary = ref(null)
const businessUsers = ref([])
const createForm = reactive({
  name: '',
  charset: 'utf8mb4',
  collation: '',
  grant_target: '',
  privilege_level: 'all',
})

const host = computed(() => summary.value?.host_stats || {})

function formatSize(bytes) {
  if (bytes == null || bytes === 0) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return (bytes / 1024 ** 3).toFixed(2) + ' GB'
}

function formatRows(n) {
  if (n == null) return '-'
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

async function load() {
  loading.value = true
  try {
    const { data } = await databaseApi.list()
    items.value = data.items
    summary.value = data.summary
  } finally {
    loading.value = false
  }
}

async function loadBusinessUsers() {
  try {
    const { data } = await accountApi.businessList()
    businessUsers.value = data.items || []
  } catch {
    businessUsers.value = []
  }
}

function openCreate() {
  createForm.name = ''
  createForm.charset = 'utf8mb4'
  createForm.collation = ''
  createForm.grant_target = ''
  createForm.privilege_level = 'all'
  loadBusinessUsers()
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.name.trim()) {
    ElMessage.warning('请填写库名')
    return
  }
  creating.value = true
  try {
    const payload = {
      name: createForm.name.trim(),
      charset: createForm.charset,
      privilege_level: createForm.privilege_level,
    }
    if (createForm.collation.trim()) payload.collation = createForm.collation.trim()
    if (createForm.grant_target) {
      const at = createForm.grant_target.lastIndexOf('@')
      payload.grant_user = createForm.grant_target.slice(0, at)
      payload.grant_host = createForm.grant_target.slice(at + 1)
    }
    await databaseApi.create(payload)
    ElMessage.success('数据库已创建')
    createVisible.value = false
    await load()
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-title { margin: 0; }
.summary-row { margin-bottom: 16px; }
.summary-card { min-height: 96px; }
.summary-label { color: #909399; font-size: 12px; margin-bottom: 4px; }
.summary-value { font-size: 20px; font-weight: 600; line-height: 1.3; }
.summary-sub { color: #909399; font-size: 12px; margin-top: 4px; }
.mini-progress { margin-top: 8px; max-width: 180px; }
.db-table { width: 100%; }
.mr-1 { margin-right: 4px; }
.ml-1 { margin-left: 4px; }
.muted { color: #909399; }
</style>
