<template>
  <div class="backups-page">
    <div class="page-header">
      <h2 class="page-title">备份管理</h2>
    </div>
    <el-tabs v-model="activeTab" class="backups-tabs" @tab-change="onTabChange">
      <el-tab-pane label="备份任务" name="jobs" lazy>
        <BackupsJobs embedded />
      </el-tab-pane>
      <el-tab-pane label="备份文件" name="files" lazy>
        <BackupsFiles embedded />
      </el-tab-pane>
      <el-tab-pane label="时间点恢复" name="pitr" lazy>
        <BackupsPitr embedded />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BackupsJobs from '@/views/BackupsJobs.vue'
import BackupsFiles from '@/views/BackupsFiles.vue'
import BackupsPitr from '@/views/BackupsPitr.vue'

const VALID_TABS = new Set(['jobs', 'files', 'pitr'])

const route = useRoute()
const router = useRouter()

function tabFromRoute() {
  const tab = String(route.query.tab || 'jobs')
  return VALID_TABS.has(tab) ? tab : 'jobs'
}

const activeTab = ref(tabFromRoute())

watch(
  () => route.query.tab,
  () => {
    activeTab.value = tabFromRoute()
  }
)

function onTabChange(name) {
  if (route.query.tab === name) return
  router.replace({ path: '/backups', query: { ...route.query, tab: name } })
}
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.page-title { margin: 0; }
.backups-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}
</style>
