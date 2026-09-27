/** 中文界面统一按东八区（Asia/Shanghai，无夏令时）显示。 */
const SHANGHAI_OFFSET_MS = 8 * 60 * 60 * 1000

function pad(n) {
  return String(n).padStart(2, '0')
}

export function fromShanghaiParts(year, month, day, hour = 0, minute = 0, second = 0) {
  return new Date(Date.UTC(year, month - 1, day, hour, minute, second) - SHANGHAI_OFFSET_MS)
}

export function shanghaiParts(date) {
  const shifted = new Date(date.getTime() + SHANGHAI_OFFSET_MS)
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
    hour: shifted.getUTCHours(),
    minute: shifted.getUTCMinutes(),
    second: shifted.getUTCSeconds(),
  }
}

/** 带时区的时间转成瞬间；没有时区的年月日时分秒按东八区理解。 */
export function toDate(value) {
  if (!value) return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  const text = String(value).trim()
  if (!text) return null
  const naive = text.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/)
  if (naive && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(text)) {
    return fromShanghaiParts(+naive[1], +naive[2], +naive[3], +naive[4], +naive[5], +naive[6])
  }
  const parsed = new Date(text)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/** 格式化为 yyyy-MM-dd HH:mm:ss，固定东八区。 */
export function formatDateTime(value) {
  const date = toDate(value)
  if (!date) return '-'
  const part = shanghaiParts(date)
  return `${part.year}-${pad(part.month)}-${pad(part.day)} ${pad(part.hour)}:${pad(part.minute)}:${pad(part.second)}`
}

export function formatClock(value) {
  const date = value instanceof Date ? value : toDate(value)
  if (!date || Number.isNaN(date.getTime())) return '--:--:--'
  const part = shanghaiParts(date)
  return `${pad(part.hour)}:${pad(part.minute)}:${pad(part.second)}`
}

/** 备份文件名时间戳 20250601_113640 → yyyy-MM-dd HH:mm:ss（文件名本身按东八区生成） */
export function formatBackupTimestamp(value) {
  if (!value) return '-'
  const matched = String(value).match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/)
  if (!matched) return value
  return `${matched[1]}-${matched[2]}-${matched[3]} ${matched[4]}:${matched[5]}:${matched[6]}`
}

const EXPIRY_LABELS = { ok: '正常', warning: '临近过期', expired: '已过期', unknown: '未知' }
const EXPIRY_TYPES = { ok: 'success', warning: 'warning', expired: 'danger', unknown: 'info' }

export function expiryLabel(status) {
  return EXPIRY_LABELS[status] || EXPIRY_LABELS.unknown
}

export function expiryTagType(status) {
  return EXPIRY_TYPES[status] || EXPIRY_TYPES.unknown
}
