/** PITR 时间点选择：日期 + 当日时间轴（秒级） */

export function formatDisplayDateTime(date) {
  if (!date || Number.isNaN(date.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function parseDisplayDateTime(value) {
  const text = (value || '').trim()
  if (!text) return null
  const [datePart, timePart = '00:00:00'] = text.split(' ')
  const [y, m, d] = datePart.split('-').map(Number)
  const [hh, mm, ss] = timePart.split(':').map(Number)
  if (!y || !m || !d) return null
  const dt = new Date(y, m - 1, d, hh || 0, mm || 0, ss || 0)
  return Number.isNaN(dt.getTime()) ? null : dt
}

export function datePartOf(value) {
  const dt = typeof value === 'string' ? parseDisplayDateTime(value) : value
  if (!dt) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`
}

export function startOfDay(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number)
  return new Date(y, m - 1, d, 0, 0, 0)
}

export function endOfDay(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number)
  return new Date(y, m - 1, d, 23, 59, 59)
}

export function parseIso(value) {
  if (!value) return null
  const dt = new Date(value)
  return Number.isNaN(dt.getTime()) ? null : dt
}

/** 某日内在全局可恢复范围内的起止时间（秒级跨度） */
export function getDayTimeRange(dateStr, earliestIso, latestIso) {
  if (!dateStr) return null
  const earliest = parseIso(earliestIso)
  const latest = parseIso(latestIso)
  if (!earliest || !latest) return null

  const start = new Date(Math.max(startOfDay(dateStr).getTime(), earliest.getTime()))
  const end = new Date(Math.min(endOfDay(dateStr).getTime(), latest.getTime()))
  if (start.getTime() > end.getTime()) return null

  const spanSec = Math.floor((end.getTime() - start.getTime()) / 1000)
  return { start, end, spanSec }
}

export function isDateDisabled(date, earliestIso, latestIso) {
  const earliest = parseIso(earliestIso)
  const latest = parseIso(latestIso)
  if (!earliest || !latest) return true
  const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0)
  const dayEnd = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59)
  return dayEnd < earliest || dayStart > latest
}

export function datetimeToOffset(dateStr, dt, dayRange) {
  if (!dayRange || !dt) return 0
  const ms = dt.getTime() - dayRange.start.getTime()
  return Math.min(dayRange.spanSec, Math.max(0, Math.floor(ms / 1000)))
}

export function offsetToDisplay(dayRange, offsetSec) {
  if (!dayRange) return ''
  const clamped = Math.min(dayRange.spanSec, Math.max(0, offsetSec))
  return formatDisplayDateTime(new Date(dayRange.start.getTime() + clamped * 1000))
}

export function formatTimeOnly(date) {
  if (!date) return '--:--:--'
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function defaultTargetTime(earliestIso, latestIso) {
  const latest = parseIso(latestIso)
  if (!latest) return ''
  return formatDisplayDateTime(latest)
}
