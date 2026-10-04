<template>
  <div class="cron-picker" :class="{ disabled }">
    <div class="cron-row">
      <el-select v-model="state.mode" :disabled="disabled" class="mode-select" @change="emitCron">
        <el-option
          v-for="mode in modeList"
          :key="mode"
          :label="MODE_OPTIONS[mode].label"
          :value="mode"
        />
      </el-select>

      <template v-if="state.mode === 'every_minutes'">
        <span class="label-text">每</span>
        <el-input-number v-model="state.interval" :min="1" :max="59" :disabled="disabled" @change="emitCron" />
        <span class="label-text">分钟</span>
      </template>

      <template v-else-if="state.mode === 'hourly'">
        <span class="label-text">在第</span>
        <el-input-number v-model="state.minute" :min="0" :max="59" :disabled="disabled" @change="emitCron" />
        <span class="label-text">分钟</span>
      </template>

      <template v-else-if="state.mode === 'every_hours'">
        <span class="label-text">每</span>
        <el-input-number v-model="state.interval" :min="1" :max="23" :disabled="disabled" @change="emitCron" />
        <span class="label-text">小时，第</span>
        <el-input-number v-model="state.minute" :min="0" :max="59" :disabled="disabled" @change="emitCron" />
        <span class="label-text">分钟</span>
      </template>

      <template v-else-if="state.mode === 'daily'">
        <el-time-picker
          v-model="timeModel"
          format="HH:mm"
          value-format="HH:mm"
          :disabled="disabled"
          placeholder="执行时间"
          class="time-picker"
          @change="onTimeChange"
        />
      </template>

      <template v-else-if="state.mode === 'weekly'">
        <el-select
          v-model="state.weekdays"
          multiple
          collapse-tags
          collapse-tags-tooltip
          :disabled="disabled"
          class="weekday-select"
          placeholder="选择星期"
          @change="emitCron"
        >
          <el-option v-for="d in WEEKDAY_OPTIONS" :key="d.value" :label="d.label" :value="d.value" />
        </el-select>
        <el-time-picker
          v-model="timeModel"
          format="HH:mm"
          value-format="HH:mm"
          :disabled="disabled"
          placeholder="执行时间"
          class="time-picker"
          @change="onTimeChange"
        />
      </template>

      <template v-else-if="state.mode === 'monthly'">
        <span class="label-text">每月</span>
        <el-input-number v-model="state.dayOfMonth" :min="1" :max="28" :disabled="disabled" @change="emitCron" />
        <span class="label-text">日</span>
        <el-time-picker
          v-model="timeModel"
          format="HH:mm"
          value-format="HH:mm"
          :disabled="disabled"
          placeholder="执行时间"
          class="time-picker"
          @change="onTimeChange"
        />
      </template>

      <template v-else-if="state.mode === 'custom'">
        <el-input
          v-model="state.custom"
          :disabled="disabled"
          class="custom-input"
          placeholder="分 时 日 月 周"
          @change="emitCron"
        />
      </template>
    </div>

    <div v-if="!disabled && state.mode !== 'custom'" class="cron-preview">
      {{ describeCron(previewCron) }}
      <code class="cron-code">{{ previewCron }}</code>
    </div>
    <div v-else-if="showAdvancedLink && state.mode !== 'custom'" class="cron-advanced">
      <el-button link type="primary" @click="switchToCustom">高级：自定义 Cron</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import {
  MODE_OPTIONS,
  WEEKDAY_OPTIONS,
  buildCron,
  describeCron,
  parseCron,
  parseTimeValue,
  timeValue,
} from '@/utils/cronSchedule'

const props = defineProps({
  modelValue: { type: String, default: '0 3 * * *' },
  disabled: { type: Boolean, default: false },
  allowedModes: {
    type: Array,
    default: () => ['every_minutes', 'hourly', 'every_hours', 'daily', 'weekly', 'monthly', 'custom'],
  },
  showAdvancedLink: { type: Boolean, default: true },
})

const emit = defineEmits(['update:modelValue'])

const state = ref(parseCron(props.modelValue))
const timeModel = ref(timeValue(state.value.hour, state.value.minute))

const modeList = computed(() => props.allowedModes.filter((m) => MODE_OPTIONS[m]))

const previewCron = computed(() => buildCron(state.value))

watch(
  () => props.modelValue,
  (val) => {
    const next = parseCron(val)
    if (next.mode !== 'custom' && !props.allowedModes.includes(next.mode)) {
      next.mode = 'custom'
      next.custom = (val || '').trim()
    }
    state.value = next
    timeModel.value = timeValue(next.hour, next.minute)
  },
  { immediate: true }
)

function onTimeChange(val) {
  const { hour, minute } = parseTimeValue(val)
  state.value.hour = hour
  state.value.minute = minute
  emitCron()
}

function switchToCustom() {
  state.value.mode = 'custom'
  state.value.custom = previewCron.value
  emitCron()
}

function emitCron() {
  if (props.disabled) return
  if (state.value.mode !== 'custom' && !props.allowedModes.includes(state.value.mode)) {
    state.value.mode = props.allowedModes[0]
  }
  if (state.value.mode === 'weekly' && (!state.value.weekdays || !state.value.weekdays.length)) {
    state.value.weekdays = [0]
  }
  emit('update:modelValue', buildCron(state.value))
}
</script>

<style scoped>
.cron-picker.disabled { opacity: 0.65; }
.cron-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.mode-select { width: 140px; }
.weekday-select { width: 180px; }
.time-picker { width: 120px; }
.custom-input { width: 220px; }
.label-text { color: #606266; font-size: 13px; white-space: nowrap; }
.cron-preview {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
.cron-code {
  margin-left: 8px;
  padding: 1px 6px;
  background: #f5f7fa;
  border-radius: 4px;
  color: #606266;
}
.cron-advanced { margin-top: 4px; }
</style>
