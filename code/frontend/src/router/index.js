import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      { path: '', name: 'Dashboard', component: () => import('@/views/Dashboard.vue') },
      { path: 'databases', name: 'Databases', component: () => import('@/views/Databases.vue') },
      { path: 'databases/:name', name: 'DatabaseDetail', component: () => import('@/views/DatabaseDetail.vue') },
      { path: 'mysql/settings', name: 'MySQLSettings', component: () => import('@/views/MySQLSettings.vue') },
      { path: 'accounts', name: 'Accounts', component: () => import('@/views/Accounts.vue') },
      { path: 'dts', name: 'Dts', component: () => import('@/views/Dts.vue') },
      { path: 'dts/:id', name: 'DtsDetail', component: () => import('@/views/DtsDetail.vue') },
      { path: 'backups', name: 'Backups', component: () => import('@/views/Backups.vue') },
      { path: 'backups/jobs', redirect: { path: '/backups', query: { tab: 'jobs' } } },
      { path: 'backups/files', redirect: { path: '/backups', query: { tab: 'files' } } },
      { path: 'backups/pitr', redirect: { path: '/backups', query: { tab: 'pitr' } } },
      { path: 'backups/log', name: 'BackupLog', component: () => import('@/views/BackupsLog.vue') },
      { path: 'storages', name: 'Storages', component: () => import('@/views/Storages.vue') },
      { path: 'system', name: 'SystemInfo', component: () => import('@/views/SystemInfo.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isLoggedIn) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'Login' && auth.isLoggedIn) {
    return { name: 'Dashboard' }
  }
})

export default router
