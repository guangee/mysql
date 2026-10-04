const GB = 1024 ** 3
const TB = 1024 ** 4

/** 备份总量：<1GB 显示 MB，<1TB 显示 GB，否则显示 TB */
export function formatBackupTotalSize(bytes) {
  const n = Number(bytes) || 0
  if (n <= 0) return '0.00 MB'
  if (n < GB) return (n / (1024 ** 2)).toFixed(2) + ' MB'
  if (n < TB) return (n / GB).toFixed(2) + ' GB'
  return (n / TB).toFixed(2) + ' TB'
}

/** 汇总备份文件在各存储源上的占用（同一文件多存储会累加） */
export function sumBackupItemsBytes(items) {
  return (items || []).reduce((total, item) => {
    const fileBytes = (item.storages || []).reduce((s, st) => s + (st.size_bytes || 0), 0)
    return total + fileBytes
  }, 0)
}
