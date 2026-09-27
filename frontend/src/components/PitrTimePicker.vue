<template>
  <div class="pitr-time-picker">
    <div class="date-row">
      <el-button :disabled="disabled || !recoverable" @click="openDateDialog">
        <el-icon class="btn-icon"><Calendar /></el-icon>
        {{ selectedDate || '选择回滚日期' }}
      </el-button>
      <span v-if="selectedDate && dayRange" class="day-range-hint">
        当日可选 {{ formatTimeOnly(dayRange.start) }} ~ {{ formatTimeOnly(dayRange.end) }}
      </span>
    </div>

    <div v-if="selectedDate && dayRange" class="time-panel">
      <div class="time-display">
        <span class="time-date">{{ selectedDate }}</span>
        <span class="time-clock">{{ clockText }}</span>
      </div>

      <div class="time-controls">
        <el-button :disabled="disabled || atMin" :icon="DArrowLeft" title="减 1 分钟" @click="nudge(-60)" />
        <el-button :disabled="disabled || atMin" :icon="ArrowLeft" title="减 1 秒" @click="nudge(-1)" />
        <el-button :disabled="disabled || atMax" :icon="ArrowRight" title="加 1 秒" @click="nudge(1)" />
        <el-button :disabled="disabled || atMax" :icon="DArrowRight" title="加 1 分钟" @click="nudge(60)" />
      </div>

      <el-slider
        v-model="offsetSec"
        :min="0"
        :max="dayRange.spanSec"
        :step="1"
        :disabled="disabled || dayRange.spanSec <= 0"
        :format-tooltip="formatSliderTooltip"
        class="time-slider"
        @input="onSliderInput"
      />
      <div class="slider-labels">
        <span>{{ formatTimeOnly(dayRange.start) }}</span>
        <span>{{ formatTimeOnly(dayRange.end) }}</span>
      </div>
    </div>

    <div v-else-if="recoverable && !selectedDate" class="empty-hint">
      请先选择回滚日期，再拖动滑块或微调按钮选定具体时间点
    </div>

    <el-dialog v-model="dateDialogVisible" title="选择回滚日期" width="420px" destroy-on-close @open="onDialogOpen">
      <el-date-picker
        v-model="pickDate"
        type="date"
        value-format="YYYY-MM-DD"
        :disabled-date="disabledDateFn"
        placeholder="选择日期"
        style="width: 100%"
      />
      <template #footer>
        <el-button @click="dateDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!pickDate" @click="confirmDate">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, Calendar, DArrowLeft, DArrowRight } from '@element-plus/icons-vue'
import {
  datePartOf,
  datetimeToOffset,
  formatDisplayDateTime,
  formatTimeOnly,
  getDayTimeRange,
  isDateDisabled,
  offsetToDisplay,
  parseDisplayDateTime,
} from '@/utils/pitrTime'

const props = defineProps({
  modelValue: { type: String, default: '' },
  earliestAt: { type: String, default: '' },
  latestAt: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  recoverable: { type: Boolean, default: true },
})

const emit = defineEmits(['update:modelValue'])

const selectedDate = ref('')
const offsetSec = ref(0)
const dateDialogVisible = ref(false)
const pickDate = ref('')

const dayRange = computed(() => {
  if (!selectedDate.value) return null
  return getDayTimeRange(selectedDate.value, props.earliestAt, props.latestAt)
})

const clockText = computed(() => {
  if (!dayRange.value) return '--:--:--'
  return formatTimeOnly(new Date(dayRange.value.start.getTime() + offsetSec.value * 1000))
})

const atMin = computed(() => offsetSec.value <= 0)
const atMax = computed(() => !dayRange.value || offsetSec.value >= dayRange.value.spanSec)

function disabledDateFn(date) {
  return isDateDisabled(date, props.earliestAt, props.latestAt)
}

function formatSliderTooltip(val) {
  if (!dayRange.value) return ''
  return formatTimeOnly(new Date(dayRange.value.start.getTime() + val * 1000))
}

function emitValue() {
  if (!dayRange.value) return
  emit('update:modelValue', offsetToDisplay(dayRange.value, offsetSec.value))
}

function onSliderInput() {
  emitValue()
}

function nudge(seconds) {
  if (!dayRange.value) return
  offsetSec.value = Math.min(dayRange.value.spanSec, Math.max(0, offsetSec.value + seconds))
  emitValue()
}

function applyModelValue(val) {
  const dt = parseDisplayDateTime(val)
  if (!dt) {
    if (!val) {
      selectedDate.value = ''
      offsetSec.value = 0
    }
    return
  }
  const date = datePartOf(dt)
  const range = getDayTimeRange(date, props.earliestAt, props.latestAt)
  if (!range) return
  selectedDate.value = date
  offsetSec.value = datetimeToOffset(date, dt, range)
}

function openDateDialog() {
  pickDate.value = selectedDate.value || ''
  dateDialogVisible.value = true
}

function onDialogOpen() {
  pickDate.value = selectedDate.value || ''
}

function confirmDate() {
  if (!pickDate.value) return
  const range = getDayTimeRange(pickDate.value, props.earliestAt, props.latestAt)
  if (!range) return
  selectedDate.value = pickDate.value
  offsetSec.value = range.spanSec
  dateDialogVisible.value = false
  emitValue()
}

watch(
  () => props.modelValue,
  (val) => applyModelValue(val),
  { immediate: true }
)

watch(
  () => [props.earliestAt, props.latestAt],
  () => {
    if (props.modelValue) {
      applyModelValue(props.modelValue)
    }
  }
)
</script>

<style scoped>
.pitr-time-picker { width: 100%; max-width: 520px; }
.date-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.btn-icon { margin-right: 4px; }
.day-range-hint { font-size: 12px; color: #909399; }
.time-panel {
  margin-top: 16px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 8px;
}
.time-display {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 12px;
}
.time-date { font-size: 14px; color: #606266; }
.time-clock {
  font-size: 28px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #303133;
  letter-spacing: 0.02em;
}
.time-controls {
  display: flex;
  justify-content: center;
  gap: 8px;
  margin-bottom: 12px;
}
.time-slider { margin: 0 4px; }
.slider-labels {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.empty-hint {
  margin-top: 8px;
  font-size: 13px;
  color: #909399;
}
</style>
