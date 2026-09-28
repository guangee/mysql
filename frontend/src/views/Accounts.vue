<template>
  <div v-loading="loading">
    <div class="page-header">
      <h2 class="page-title">账号与凭证</h2>
      <div class="page-hint">展开业务用户可查看其对各数据库/表的权限树</div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="业务用户" name="business">
        <div class="tab-toolbar">
          <el-button type="primary" size="small" @click="openCreateUser">新建用户</el-button>
        </div>
        <el-table :data="businessUsers" stripe row-key="rowKey" @expand-change="onExpandChange">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="grant-panel">
                <div v-if="!(row.grant_tree || []).length" class="muted">暂无授权信息</div>
                <el-tree
                  v-else
                  :data="row.grant_tree"
                  node-key="id"
                  default-expand-all
                  :expand-on-click-node="false"
                  class="grant-tree"
                >
                  <template #default="{ data }">
                    <div class="grant-node">
                      <el-tag size="small" :type="scopeTagType(data.scope)" class="scope-tag">
                        {{ scopeLabel(data.scope) }}
                      </el-tag>
                      <span class="grant-label">{{ data.label }}</span>
                      <el-tag v-if="data.level_label" size="small" effect="plain" class="level-tag">
                        {{ data.level_label }}
                      </el-tag>
                      <el-tag v-if="data.with_grant_option" size="small" type="warning" effect="plain">
                        GRANT OPTION
                      </el-tag>
                      <span v-if="data.privileges" class="priv-text" :title="data.privileges">
                        {{ data.privileges }}
                      </span>
                    </div>
                  </template>
                </el-tree>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="user" label="用户" min-width="120">
            <template #default="{ row }"><code>{{ row.user }}</code></template>
          </el-table-column>
          <el-table-column prop="host" label="Host" width="140" />
          <el-table-column label="授权库数" width="100" align="right">
            <template #default="{ row }">{{ row.database_count ?? 0 }}</template>
          </el-table-column>
          <el-table-column prop="grants_summary" label="权限摘要" min-width="180" show-overflow-tooltip />
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button size="small" @click="openPassword('business', row)">修改密码</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="系统账号" name="system">
        <el-table :data="systemAccounts" stripe>
          <el-table-column prop="role" label="角色" width="120" />
          <el-table-column prop="user" label="用户">
            <template #default="{ row }"><code>{{ row.user }}</code></template>
          </el-table-column>
          <el-table-column prop="host" label="Host" width="140" />
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button v-if="auth.isSuperuser" size="small" @click="openPassword('system', row)">修改密码</el-button>
              <span v-else class="muted">需超级管理员</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="createVisible" title="新建业务用户" width="480px" destroy-on-close>
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="用户名" required>
          <el-input v-model="createForm.user" placeholder="字母、数字、下划线" />
        </el-form-item>
        <el-form-item label="Host">
          <el-select v-model="createForm.host" allow-create filterable>
            <el-option label="任意主机 (%)" value="%" />
            <el-option label="本机 (localhost)" value="localhost" />
          </el-select>
        </el-form-item>
        <el-form-item label="密码" required>
          <el-input v-model="createForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认密码" required>
          <el-input v-model="createForm.confirm_password" type="password" show-password />
        </el-form-item>
        <el-divider content-position="left">库权限（可选）</el-divider>
        <el-form-item label="授权数据库">
          <el-select v-model="createForm.database" clearable filterable placeholder="选择业务库">
            <el-option v-for="db in databases" :key="db.name" :label="db.name" :value="db.name" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="createForm.database" label="权限">
          <el-select v-model="createForm.privilege_level">
            <el-option label="全部权限" value="all" />
            <el-option label="读写" value="read_write" />
            <el-option label="只读" value="read_only" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreateUser">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="420px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="新密码">
          <el-input v-model="form.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="form.confirm_password" type="password" show-password />
        </el-form-item>
        <el-form-item v-if="passwordTarget?.type === 'system' && passwordTarget?.role === 'root'" label="登录密码">
          <el-input v-model="form.admin_password" type="password" show-password placeholder="修改 root 需验证" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitPassword">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { accountApi, databaseApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const creating = ref(false)
const tab = ref('business')
const businessUsers = ref([])
const systemAccounts = ref([])
const databases = ref([])
const createVisible = ref(false)
const dialogVisible = ref(false)
const submitting = ref(false)
const passwordTarget = ref(null)
const form = reactive({ new_password: '', confirm_password: '', admin_password: '' })
const createForm = reactive({
  user: '',
  host: '%',
  password: '',
  confirm_password: '',
  database: '',
  privilege_level: 'all',
})

const dialogTitle = computed(() => {
  if (!passwordTarget.value) return '修改密码'
  const t = passwordTarget.value
  return t.type === 'business' ? `修改 ${t.user}@${t.host}` : `修改系统账号 (${t.role})`
})

function scopeLabel(scope) {
  return { global: '全局', database: '数据库', table: '表', other: '其他' }[scope] || scope
}

function scopeTagType(scope) {
  return { global: 'danger', database: 'primary', table: 'success', other: 'info' }[scope] || 'info'
}

function onExpandChange() {
  // 预留：展开时如需懒加载可接这里
}

async function load() {
  loading.value = true
  try {
    const [biz, sys] = await Promise.all([accountApi.businessList(), accountApi.systemList()])
    businessUsers.value = (biz.data.items || []).map((item) => ({
      ...item,
      rowKey: `${item.user}@${item.host}`,
      grant_tree: item.grant_tree || [],
    }))
    systemAccounts.value = sys.data.items
  } finally {
    loading.value = false
  }
}

async function loadDatabases() {
  try {
    const { data } = await databaseApi.list()
    databases.value = data.items || []
  } catch {
    databases.value = []
  }
}

function openCreateUser() {
  createForm.user = ''
  createForm.host = '%'
  createForm.password = ''
  createForm.confirm_password = ''
  createForm.database = ''
  createForm.privilege_level = 'all'
  loadDatabases()
  createVisible.value = true
}

async function submitCreateUser() {
  if (!createForm.user.trim()) {
    ElMessage.warning('请填写用户名')
    return
  }
  creating.value = true
  try {
    const payload = {
      user: createForm.user.trim(),
      host: createForm.host || '%',
      password: createForm.password,
      confirm_password: createForm.confirm_password,
      privilege_level: createForm.privilege_level,
    }
    if (createForm.database) payload.database = createForm.database
    await accountApi.createBusiness(payload)
    ElMessage.success('用户已创建')
    createVisible.value = false
    await load()
  } finally {
    creating.value = false
  }
}

function openPassword(type, row) {
  passwordTarget.value = type === 'business'
    ? { type, user: row.user, host: row.host }
    : { type, role: row.role, user: row.user, host: row.host }
  form.new_password = ''
  form.confirm_password = ''
  form.admin_password = ''
  dialogVisible.value = true
}

async function submitPassword() {
  submitting.value = true
  try {
    const t = passwordTarget.value
    if (t.type === 'business') {
      await accountApi.changeBusinessPassword(t.user, t.host, form)
    } else {
      await accountApi.changeSystemPassword(t.role, form)
    }
    ElMessage.success('密码已修改')
    dialogVisible.value = false
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-header { margin-bottom: 8px; }
.page-title { margin: 0 0 4px; }
.page-hint { color: #909399; font-size: 13px; margin-bottom: 12px; }
.tab-toolbar { margin-bottom: 12px; }
.muted { color: #909399; font-size: 12px; }
.grant-panel {
  padding: 8px 12px 12px 36px;
  background: #fafbfc;
}
.grant-tree { background: transparent; }
.grant-node {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 2px 0;
  width: 100%;
}
.scope-tag { flex-shrink: 0; }
.grant-label { font-weight: 500; color: #303133; }
.level-tag { flex-shrink: 0; }
.priv-text {
  color: #909399;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}
</style>
