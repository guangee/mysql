<template>
  <div v-loading="loading">
    <div class="page-header">
      <div class="header-left">
        <el-button link type="primary" @click="router.push('/databases')">← 返回列表</el-button>
        <h2 class="page-title"><code>{{ dbName }}</code></h2>
        <div v-if="database" class="db-meta">
          {{ database.charset }} / {{ database.collation }} · {{ database.table_count }} 表 · {{ database.size_display }}
        </div>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="表结构" name="tables">
        <el-table :data="tables" stripe border @row-click="openStructure">
          <el-table-column prop="name" label="表名" min-width="160">
            <template #default="{ row }"><code>{{ row.name }}</code></template>
          </el-table-column>
          <el-table-column prop="engine" label="引擎" width="100" />
          <el-table-column prop="row_count" label="行数(约)" width="100" align="right" />
          <el-table-column prop="data_size_display" label="数据" width="100" align="right" />
          <el-table-column prop="index_size_display" label="索引" width="100" align="right" />
          <el-table-column prop="size_display" label="总大小" width="100" align="right" />
          <el-table-column prop="comment" label="注释" min-width="140" show-overflow-tooltip />
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click.stop="openStructure(row)">结构</el-button>
              <el-button size="small" link @click.stop="previewTable(row)">预览</el-button>
              <el-button size="small" link @click.stop="exportTable(row)">导出 Excel</el-button>
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
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { databaseApi } from '@/api'

const route = useRoute()
const router = useRouter()
const dbName = computed(() => route.params.name)

const loading = ref(false)
const querying = ref(false)
const exporting = ref(false)
const activeTab = ref('tables')
const database = ref(null)
const tables = ref([])
const structureVisible = ref(false)
const structure = ref(null)
const sqlText = ref('')
const queryLimit = ref(500)
const queryResult = reactive({ columns: [], rows: [], row_count: 0, truncated: false })

const resultRows = computed(() =>
  queryResult.rows.map((row) => {
    const obj = {}
    queryResult.columns.forEach((col, i) => {
      obj[col] = row[i]
    })
    return obj
  }),
)

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function loadDetail() {
  loading.value = true
  try {
    const { data } = await databaseApi.detail(dbName.value)
    database.value = data.database
    tables.value = data.tables || []
  } finally {
    loading.value = false
  }
}

async function openStructure(row) {
  const { data } = await databaseApi.tableStructure(dbName.value, row.name)
  structure.value = data
  structureVisible.value = true
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
  loadDetail()
})

onMounted(loadDetail)
</script>

<style scoped>
.page-header { margin-bottom: 16px; }
.header-left { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.page-title { margin: 0; font-size: 22px; }
.db-meta { font-size: 13px; color: #909399; }
.sql-tip { margin-bottom: 12px; }
.sql-editor { font-family: ui-monospace, monospace; }
.sql-toolbar { display: flex; align-items: center; gap: 12px; margin: 12px 0; flex-wrap: wrap; }
.limit-label { color: #606266; font-size: 13px; }
.result-meta { margin-bottom: 8px; font-size: 13px; color: #606266; }
.result-table { width: 100%; }
.section-title { margin: 16px 0 8px; font-size: 14px; font-weight: 600; }
.create-sql {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  overflow: auto;
  max-height: 240px;
}
.ml-1 { margin-left: 6px; }
</style>
