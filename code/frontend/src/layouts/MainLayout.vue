<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <el-icon><Coin /></el-icon>
        <span>MySQL 控制台</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :default-openeds="openedMenus"
        router
        background-color="#1e293b"
        text-color="#cbd5e1"
        active-text-color="#fff"
      >
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon>
          <span>监控中心</span>
        </el-menu-item>

        <el-sub-menu index="data">
          <template #title>
            <el-icon><Coin /></el-icon>
            <span>数据中心</span>
          </template>
          <el-menu-item index="/databases">业务数据库</el-menu-item>
          <el-menu-item index="/accounts">账号与凭证</el-menu-item>
          <el-menu-item index="/mysql/settings">参数配置</el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="backup">
          <template #title>
            <el-icon><Upload /></el-icon>
            <span>备份中心</span>
          </template>
          <el-menu-item index="/backups">备份管理</el-menu-item>
          <el-menu-item index="/backups/log">备份日志</el-menu-item>
          <el-menu-item index="/storages">对象存储</el-menu-item>
          <el-menu-item index="/dts">数据同步</el-menu-item>
        </el-sub-menu>

        <el-menu-item index="/system">
          <el-icon><Setting /></el-icon>
          <span>系统管理</span>
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

const openedMenus = ['data', 'backup']

const activeMenu = computed(() => {
  const path = route.path
  if (path === '/' || path === '') return '/'
  if (path.startsWith('/databases')) return '/databases'
  if (path.startsWith('/accounts')) return '/accounts'
  if (path.startsWith('/mysql/settings')) return '/mysql/settings'
  if (path.startsWith('/backups/log')) return '/backups/log'
  if (path.startsWith('/backups')) return '/backups'
  if (path.startsWith('/storages')) return '/storages'
  if (path.startsWith('/dts')) return '/dts'
  if (path.startsWith('/system')) return '/system'
  return path
})

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout { height: 100vh; }
.aside { background: #1e293b; overflow-y: auto; }
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
