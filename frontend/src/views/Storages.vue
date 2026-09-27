<template>
  <div v-loading="loading">
    <div class="toolbar">
      <h2 class="page-title">对象存储</h2>
      <div>
        <el-button @click="importEnv">从 .env 导入</el-button>
        <el-button type="primary" @click="openForm()">新建存储</el-button>
      </div>
    </div>
    <el-table :data="items" stripe>
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="endpoint" label="Endpoint" min-width="160">
        <template #default="{ row }"><code>{{ row.endpoint }}</code></template>
      </el-table-column>
      <el-table-column prop="bucket" label="Bucket" width="120" />
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '是' : '否' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="连通性" width="120">
        <template #default="{ row }">
          <span v-if="row.last_test_at" :class="row.last_test_ok ? 'ok' : 'err'">
            {{ row.last_test_ok ? '正常' : '失败' }}
          </span>
          <span v-else class="muted">未测试</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="240">
        <template #default="{ row }">
          <el-button size="small" @click="testStorage(row)">测试</el-button>
          <el-button size="small" @click="openForm(row)">编辑</el-button>
          <el-popconfirm title="确定删除？" @confirm="remove(row.id)">
            <template #reference>
              <el-button size="small" type="danger">删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑存储' : '新建存储'" width="520px">
      <el-form :model="form" label-width="120px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="MC 别名"><el-input v-model="form.alias" /></el-form-item>
        <el-form-item label="Endpoint"><el-input v-model="form.endpoint" placeholder="s3.example.com 或 oss-cn-xxx.aliyuncs.com" /></el-form-item>
        <el-form-item label="Bucket"><el-input v-model="form.bucket" /></el-form-item>
        <el-form-item label="Region"><el-input v-model="form.region" /></el-form-item>
        <el-form-item label="Access Key"><el-input v-model="form.access_key" :placeholder="form.id ? '留空不变' : ''" /></el-form-item>
        <el-form-item label="Secret Key"><el-input v-model="form.secret_key" type="password" show-password :placeholder="form.id ? '留空不变' : ''" /></el-form-item>
        <el-form-item label="使用 SSL"><el-switch v-model="form.use_ssl" /></el-form-item>
        <el-form-item label="Path Style"><el-switch v-model="form.force_path_style" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { storageApi } from '@/api'

const loading = ref(false)
const submitting = ref(false)
const dialogVisible = ref(false)
const items = ref([])
const form = reactive({
  id: null,
  name: '',
  alias: 's3',
  endpoint: '',
  bucket: 'mysql',
  region: 'us-east-1',
  access_key: '',
  secret_key: '',
  use_ssl: false,
  force_path_style: true,
  enabled: true,
})

async function load() {
  loading.value = true
  try {
    const { data } = await storageApi.list()
    items.value = data
  } finally {
    loading.value = false
  }
}

function openForm(row = null) {
  Object.assign(form, {
    id: row?.id || null,
    name: row?.name || '',
    alias: row?.alias || 's3',
    endpoint: row?.endpoint || '',
    bucket: row?.bucket || 'mysql',
    region: row?.region || 'us-east-1',
    access_key: '',
    secret_key: '',
    use_ssl: row?.use_ssl ?? false,
    force_path_style: row?.force_path_style ?? true,
    enabled: row?.enabled ?? true,
  })
  dialogVisible.value = true
}

async function submit() {
  submitting.value = true
  try {
    const payload = { ...form }
    delete payload.id
    if (form.id) {
      if (!payload.access_key) delete payload.access_key
      if (!payload.secret_key) delete payload.secret_key
      await storageApi.update(form.id, payload)
    } else {
      await storageApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

async function testStorage(row) {
  const { data } = await storageApi.test(row.id)
  ElMessage[data.ok ? 'success' : 'error'](data.message)
  await load()
}

async function remove(id) {
  await storageApi.remove(id)
  ElMessage.success('已删除')
  await load()
}

async function importEnv() {
  const { data } = await storageApi.importEnv()
  ElMessage.info(data.detail)
  await load()
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-title { margin: 0; }
.ok { color: #67c23a; }
.err { color: #f56c6c; }
.muted { color: #909399; }
</style>
