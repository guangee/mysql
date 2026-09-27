<template>
  <div v-loading="loading">
    <div class="toolbar">
      <h2 class="page-title">备份日志</h2>
      <el-button :icon="Refresh" circle @click="load" />
    </div>
    <pre class="log-view">{{ content || '(空)' }}</pre>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { backupApi } from '@/api'

const loading = ref(false)
const content = ref('')
let timer = null

async function load() {
  loading.value = true
  try {
    const { data } = await backupApi.log()
    content.value = data.content
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 10000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-title { margin: 0; }
.log-view {
  background: #0f172a;
  color: #e2e8f0;
  padding: 16px;
  border-radius: 8px;
  max-height: 70vh;
  overflow: auto;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
