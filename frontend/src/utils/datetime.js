/** 格式化为 yyyy-MM-dd HH:mm:ss */
export function formatDateTime(value) {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 备份文件名时间戳 20250601_113640 → yyyy-MM-dd HH:mm:ss */
export function formatBackupTimestamp(value) {
  if (!value) return '-'
  const m = String(value).match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/)
  if (!m) return value
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`
}

const EXPIRY_LABELS = { ok: '正常', warning: '临近过期', expired: '已过期', unknown: '未知' }
const EXPIRY_TYPES = { ok: 'success', warning: 'warning', expired: 'danger', unknown: 'info' }

export function expiryLabel(status) {
  return EXPIRY_LABELS[status] || EXPIRY_LABELS.unknown
}

export function expiryTagType(status) {
  return EXPIRY_TYPES[status] || EXPIRY_TYPES.unknown
}
