<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <el-icon><Coin /></el-icon>
        <span>MySQL 控制台</span>
      </div>
      <el-menu :default-active="activeMenu" router background-color="#1e293b" text-color="#cbd5e1" active-text-color="#fff">
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon>
          <span>概览</span>
        </el-menu-item>
        <el-menu-item index="/databases">
          <el-icon><Coin /></el-icon>
          <span>业务数据库</span>
        </el-menu-item>
        <el-menu-item index="/mysql/settings">
          <el-icon><Setting /></el-icon>
          <span>MySQL 参数</span>
        </el-menu-item>
        <el-menu-item index="/accounts">
          <el-icon><User /></el-icon>
          <span>账号与凭证</span>
        </el-menu-item>
        <el-sub-menu index="backups">
          <template #title>
            <el-icon><Upload /></el-icon>
            <span>备份中心</span>
          </template>
          <el-menu-item index="/backups/jobs">备份任务</el-menu-item>
          <el-menu-item index="/backups/files">备份文件</el-menu-item>
          <el-menu-item index="/backups/pitr">时间点恢复</el-menu-item>
          <el-menu-item index="/backups/log">备份日志</el-menu-item>
        </el-sub-menu>
        <el-menu-item index="/storages">
          <el-icon><FolderOpened /></el-icon>
          <span>对象存储</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div></div>
        <div class="header-right">
          <span class="username">{{ auth.user?.username }}</span>
          <el-button type="danger" plain size="small" @click="handleLogout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const activeMenu = computed(() => {
  if (route.path.startsWith('/databases')) return '/databases'
  if (route.path.startsWith('/backups')) return route.path
  return route.path
})

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout { height: 100vh; }
.aside { background: #1e293b; }
.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #fff;
  font-weight: 600;
  padding: 20px 16px;
  font-size: 16px;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #ebeef5;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.username { color: #606266; font-size: 14px; }
.main { background: #f4f6f9; }
</style>
