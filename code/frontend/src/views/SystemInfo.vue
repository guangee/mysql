<template>
  <div>
    <h2 class="page-title">系统信息</h2>
    <p class="page-hint">查看控制台运行状态、组件版本与当前登录信息。</p>

    <el-row :gutter="16">
      <el-col :span="12" :xs="24">
        <el-card shadow="never" v-loading="loading">
          <div class="section-title">运行状态</div>
          <div class="row"><span>健康状态</span><el-tag :type="health?.ok ? 'success' : 'danger'" size="small">{{ health?.ok ? '正常' : '异常' }}</el-tag></div>
          <div class="row"><span>运行模式</span><code>{{ health?.runtime_mode || '-' }}</code></div>
          <div class="row"><span>一体交付</span><span>{{ health?.allinone ? '是' : '否' }}</span></div>
          <div class="row">
            <span>MySQL</span>
            <el-tag :type="health?.mysql?.ready ? 'success' : 'warning'" size="small">
              {{ health?.mysql?.ready ? '就绪' : (health?.mysql?.error || '未就绪') }}
            </el-tag>
          </div>
          <div class="row"><span>控制台</span><el-tag :type="health?.console?.ok ? 'success' : 'danger'" size="small">{{ health?.console?.ok ? '正常' : '异常' }}</el-tag></div>
        </el-card>
      </el-col>
      <el-col :span="12" :xs="24">
        <el-card shadow="never">
          <div class="section-title">当前账号</div>
          <div class="row"><span>用户名</span><span>{{ auth.user?.username || '-' }}</span></div>
          <div class="row"><span>角色</span><span>{{ auth.user?.is_superuser ? '超级管理员' : '普通用户' }}</span></div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" v-loading="loading">
      <div class="section-title">组件版本</div>
      <div class="row"><span>系统版本</span><code>{{ v.app }}</code></div>
      <div class="row"><span>操作系统</span><code>{{ v.os }}</code></div>
      <div class="row"><span>内核</span><code>{{ v.kernel }}</code></div>
      <div class="row"><span>MySQL</span><code>{{ v.mysql }}</code></div>
      <div class="row"><span>Redis</span><code>{{ v.redis }}</code></div>
      <div class="row"><span>XtraBackup</span><code>{{ v.xtrabackup }}</code></div>
      <div class="row"><span>Nginx</span><code>{{ v.nginx }}</code></div>
      <div class="row"><span>Python</span><code>{{ v.python }}</code></div>
      <div class="row"><span>Django</span><code>{{ v.django }}</code></div>
      <div class="row"><span>Celery</span><code>{{ v.celery }}</code></div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { systemApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const health = ref(null)

const v = computed(() => {
  const versions = health.value?.versions || {}
  const dash = (value) => (value && String(value).trim()) || '-'
  return {
    app: dash(versions.app || health.value?.console?.version),
    os: dash(versions.os),
    kernel: dash(versions.kernel),
    mysql: dash(versions.mysql || health.value?.mysql?.version),
    redis: dash(versions.redis || health.value?.redis?.version),
    xtrabackup: dash(versions.xtrabackup),
    nginx: dash(versions.nginx),
    python: dash(versions.python),
    django: dash(versions.django),
    celery: dash(versions.celery),
  }
})

async function load() {
  loading.value = true
  try {
    const { data } = await systemApi.health()
    health.value = data
  } catch (e) {
    const payload = e?.response?.data
    health.value = payload && typeof payload === 'object'
      ? payload
      : { ok: false, mysql: { ready: false, error: e?.message || '加载失败' }, console: { ok: false } }
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 8px; font-size: 20px; font-weight: 600; }
.page-hint { margin: 0 0 16px; color: #909399; font-size: 13px; }
.section-title { font-weight: 600; margin-bottom: 12px; color: #303133; }
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid #f0f2f5;
  font-size: 14px;
  color: #606266;
}
.row:last-child { border-bottom: none; }
code { color: #303133; text-align: right; word-break: break-all; }
.el-card { margin-bottom: 16px; }
</style>
