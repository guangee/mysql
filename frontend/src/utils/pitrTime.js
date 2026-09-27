/** PITR 时间点选择：日期 + 当日时间轴（秒级），墙钟时间为东八区。 */

import { formatClock, formatDateTime, fromShanghaiParts, shanghaiParts, toDate } from '@/utils/datetime'

function pad(n) {
  return String(n).padStart(2, '0')
}

export function formatDisplayDateTime(date) {
  return formatDateTime(date)
}

export function parseDisplayDateTime(value) {
  return toDate(value)
}

export function datePartOf(value) {
  const date = typeof value === 'string' ? toDate(value) : value
  if (!date || Number.isNaN(date.getTime())) return ''
  const part = shanghaiParts(date)
  return `${part.year}-${pad(part.month)}-${pad(part.day)}`
}

export function startOfDay(dateStr) {
  const [year, month, day] = dateStr.split('-').map(Number)
  return fromShanghaiParts(year, month, day, 0, 0, 0)
}

export function endOfDay(dateStr) {
  const [year, month, day] = dateStr.split('-').map(Number)
  return fromShanghaiParts(year, month, day, 23, 59, 59)
}

export function parseIso(value) {
  return toDate(value)
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
  if (!earliest || !latest || !date) return true
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  return day < datePartOf(earliest) || day > datePartOf(latest)
}

export function datetimeToOffset(dateStr, date, dayRange) {
  if (!dayRange || !date) return 0
  const ms = date.getTime() - dayRange.start.getTime()
  return Math.min(dayRange.spanSec, Math.max(0, Math.floor(ms / 1000)))
}

export function offsetToDisplay(dayRange, offsetSec) {
  if (!dayRange) return ''
  const clamped = Math.min(dayRange.spanSec, Math.max(0, offsetSec))
  return formatDateTime(new Date(dayRange.start.getTime() + clamped * 1000))
}

export function formatTimeOnly(date) {
  return formatClock(date)
}

export function defaultTargetTime(earliestIso, latestIso) {
  const latest = parseIso(latestIso)
  if (!latest) return ''
  return formatDateTime(latest)
}
