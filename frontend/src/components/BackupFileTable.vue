<template>
  <div class="backup-section">
    <el-table :data="pageItems" stripe :row-class-name="rowClassName" empty-text="">
      <template #empty>
        <el-empty :description="emptyText" :image-size="64" />
      </template>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="expiryTagType(row.expiry_status)" size="small" effect="dark">
            {{ expiryLabel(row.expiry_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="filename" label="文件名" min-width="220">
        <template #default="{ row }"><code>{{ row.filename }}</code></template>
      </el-table-column>
      <el-table-column label="备份时间" width="170" class-name="time-col">
        <template #default="{ row }">
          <span class="time-cell">{{ formatBackupTimestamp(row.timestamp) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="预计过期" width="170" class-name="time-col">
        <template #default="{ row }">
          <span class="time-cell">{{ formatDateTime(row.expires_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="保留天数" width="90" align="center">
        <template #default="{ row }">{{ row.retention_days ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="存储源" min-width="120">
        <template #default="{ row }">
          <el-tag v-for="s in row.storages" :key="s.storage_id" size="small" class="mr-1">
            {{ s.storage_name }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="100">
        <template #default="{ row }">{{ formatSize(row.storages?.[0]?.size_bytes) }}</template>
      </el-table-column>
      <el-table-column label="操作" min-width="260" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="showRestore"
            size="small"
            type="warning"
            link
            @click="$emit('restore', row)"
          >恢复到这个状态</el-button>
          <el-button
            v-for="s in row.storages"
            :key="s.storage_id"
            size="small"
            type="primary"
            link
            @click="$emit('download', { storageId: s.storage_id, key: s.key, filename: row.filename, storageName: s.storage_name })"
          >下载</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="items.length" class="pagination-bar">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="items.length"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        small
      />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import {
  formatBackupTimestamp,
  formatDateTime,
  expiryLabel,
  expiryTagType,
} from '@/utils/datetime'

const props = defineProps({
  items: { type: Array, default: () => [] },
  emptyText: { type: String, default: '暂无数据' },
  showRestore: { type: Boolean, default: false },
})

defineEmits(['download', 'restore'])

const currentPage = ref(1)
const pageSize = ref(20)

watch(
  () => props.items,
  () => {
    currentPage.value = 1
  },
)

const pageItems = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return props.items.slice(start, start + pageSize.value)
})

function formatSize(bytes) {
  if (!bytes) return '-'
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return (bytes / 1024 ** 3).toFixed(2) + ' GB'
}

function rowClassName({ row }) {
  if (row.expiry_status === 'expired') return 'row-expired'
  if (row.expiry_status === 'warning') return 'row-warning'
  if (row.expiry_status === 'ok') return 'row-ok'
  return ''
}
</script>

<style scoped>
.backup-section { margin-bottom: 8px; }
.mr-1 { margin-right: 4px; }
.time-cell { white-space: nowrap; }
:deep(.time-col .cell) { white-space: nowrap; }
.pagination-bar { display: flex; justify-content: flex-end; margin-top: 16px; }
:deep(.row-ok) { --el-table-tr-bg-color: #f0f9eb; }
:deep(.row-warning) { --el-table-tr-bg-color: #fdf6ec; }
:deep(.row-expired) { --el-table-tr-bg-color: #fef0f0; }
</style>
