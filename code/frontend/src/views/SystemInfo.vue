<template>
  <div>
    <h2 class="page-title">系统信息</h2>
    <p class="page-hint">查看控制台运行状态与当前登录信息。</p>

    <el-row :gutter="16">
      <el-col :span="12" :xs="24">
        <el-card shadow="never" v-loading="loading">
          <div class="section-title">运行状态</div>
          <div class="row"><span>健康状态</span><el-tag :type="health?.ok ? 'success' : 'danger'" size="small">{{ health?.ok ? '正常' : '异常' }}</el-tag></div>
          <div class="row"><span>运行模式</span><code>{{ health?.runtime_mode || '-' }}</code></div>
          <div class="row"><span>一体交付</span><span>{{ health?.allinone ? '是' : '否' }}</span></div>
          <div class="row"><span>MySQL</span><el-tag :type="health?.mysql?.ready ? 'success' : 'warning'" size="small">{{ health?.mysql?.ready ? '就绪' : (health?.mysql?.error || '未就绪') }}</el-tag></div>
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
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { systemApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const health = ref(null)

async function load() {
  loading.value = true
  try {
    health.value = await systemApi.health()
  } catch (e) {
    health.value = { ok: false, mysql: { ready: false, error: e?.message || '加载失败' }, console: { ok: false } }
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
  padding: 10px 0;
  border-bottom: 1px solid #f0f2f5;
  font-size: 14px;
  color: #606266;
}
.row:last-child { border-bottom: none; }
code { color: #303133; }
.el-card { margin-bottom: 16px; }
</style>
