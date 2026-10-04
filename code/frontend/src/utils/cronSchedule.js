/** Cron 可视化配置：分 时 日 月 周 */

export const WEEKDAY_OPTIONS = [
  { value: 0, label: '周日' },
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
]

export const MODE_OPTIONS = {
  every_minutes: { label: '每 N 分钟', short: (s) => `每 ${s.interval} 分钟` },
  hourly: { label: '每小时', short: (s) => `每小时第 ${s.minute} 分钟` },
  every_hours: { label: '每 N 小时', short: (s) => `每 ${s.interval} 小时（第 ${s.minute} 分钟）` },
  daily: { label: '每天', short: (s) => `每天 ${pad2(s.hour)}:${pad2(s.minute)}` },
  weekly: { label: '每周', short: (s) => `每周${formatWeekdays(s.weekdays)} ${pad2(s.hour)}:${pad2(s.minute)}` },
  monthly: { label: '每月', short: (s) => `每月 ${s.dayOfMonth} 日 ${pad2(s.hour)}:${pad2(s.minute)}` },
  custom: { label: '自定义 Cron', short: (s) => s.custom || '-' },
}

function pad2(n) {
  return String(n).padStart(2, '0')
}

function defaultState() {
  return {
    mode: 'daily',
    minute: 0,
    hour: 3,
    dayOfMonth: 1,
    weekdays: [0],
    interval: 30,
    custom: '0 3 * * *',
  }
}

export function buildCron(state) {
  const s = { ...defaultState(), ...state }
  switch (s.mode) {
    case 'every_minutes':
      return `*/${clamp(s.interval, 1, 59)} * * * *`
    case 'hourly':
      return `${clamp(s.minute, 0, 59)} * * * *`
    case 'every_hours':
      return `${clamp(s.minute, 0, 59)} */${clamp(s.interval, 1, 23)} * * *`
    case 'daily':
      return `${clamp(s.minute, 0, 59)} ${clamp(s.hour, 0, 23)} * * *`
    case 'weekly': {
      const days = [...new Set((s.weekdays || [0]).map(Number))].sort((a, b) => a - b)
      return `${clamp(s.minute, 0, 59)} ${clamp(s.hour, 0, 23)} * * ${days.join(',') || '0'}`
    }
    case 'monthly':
      return `${clamp(s.minute, 0, 59)} ${clamp(s.hour, 0, 23)} ${clamp(s.dayOfMonth, 1, 28)} * *`
    case 'custom':
      return (s.custom || '').trim()
    default:
      return '0 3 * * *'
  }
}

export function parseCron(cron) {
  const value = (cron || '').trim()
  const base = defaultState()
  if (!value) return base

  const parts = value.split(/\s+/)
  if (parts.length !== 5) {
    return { ...base, mode: 'custom', custom: value }
  }

  const [min, hour, dom, month, dow] = parts

  const minStep = min.match(/^\*\/(\d+)$/)
  if (minStep && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return { ...base, mode: 'every_minutes', interval: Number(minStep[1]) }
  }

  if (/^\d+$/.test(min) && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return { ...base, mode: 'hourly', minute: Number(min) }
  }

  const hourStep = hour.match(/^\*\/(\d+)$/)
  if (/^\d+$/.test(min) && hourStep && dom === '*' && month === '*' && dow === '*') {
    return { ...base, mode: 'every_hours', minute: Number(min), interval: Number(hourStep[1]) }
  }

  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && month === '*' && dow === '*') {
    return { ...base, mode: 'daily', minute: Number(min), hour: Number(hour) }
  }

  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && month === '*' && dow !== '*') {
    const weekdays = dow.split(',').map((d) => Number(d.replace(/^7$/, '0'))).filter((d) => d >= 0 && d <= 6)
    return {
      ...base,
      mode: 'weekly',
      minute: Number(min),
      hour: Number(hour),
      weekdays: weekdays.length ? weekdays : [0],
    }
  }

  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && /^\d+$/.test(dom) && month === '*' && dow === '*') {
    return {
      ...base,
      mode: 'monthly',
      minute: Number(min),
      hour: Number(hour),
      dayOfMonth: Number(dom),
    }
  }

  return { ...base, mode: 'custom', custom: value }
}

export function describeCron(cron) {
  const state = parseCron(cron)
  const meta = MODE_OPTIONS[state.mode]
  if (!meta) return cron || '-'
  if (state.mode === 'custom') return `自定义：${state.custom || '-'}`
  return meta.short(state)
}

export function timeValue(hour, minute) {
  return `${pad2(hour)}:${pad2(minute)}`
}

export function parseTimeValue(value) {
  const [h, m] = (value || '03:00').split(':').map(Number)
  return { hour: Number.isFinite(h) ? h : 3, minute: Number.isFinite(m) ? m : 0 }
}

function clamp(n, min, max) {
  return Math.min(max, Math.max(min, Number(n) || min))
}

function formatWeekdays(days) {
  const map = Object.fromEntries(WEEKDAY_OPTIONS.map((d) => [d.value, d.label]))
  return (days || []).map((d) => map[d] || d).join('、')
}
