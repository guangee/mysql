<template>
  <div ref="chartRef" class="metric-line-chart" :style="{ height: `${height}px` }" />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  title: { type: String, default: '' },
  series: { type: Array, default: () => [] },
  yAxisLeftName: { type: String, default: '' },
  yAxisRightName: { type: String, default: '' },
  height: { type: Number, default: 240 },
  emptyText: { type: String, default: '暂无历史数据，请稍候…' },
})

const chartRef = ref(null)
let chart = null
let resizeObserver = null

function buildOption() {
  const hasData = props.series.some((s) => s.data?.length)
  if (!hasData) {
    return {
      title: {
        text: props.title,
        left: 0,
        textStyle: { fontSize: 14, fontWeight: 600, color: '#303133' },
      },
      graphic: {
        type: 'text',
        left: 'center',
        top: 'middle',
        style: { text: props.emptyText, fill: '#909399', fontSize: 13 },
      },
    }
  }

  const useRightAxis = props.series.some((s) => s.yAxisIndex === 1)
  return {
    title: {
      text: props.title,
      left: 0,
      textStyle: { fontSize: 14, fontWeight: 600, color: '#303133' },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      top: 4,
      right: 0,
      icon: 'circle',
      itemWidth: 8,
      textStyle: { fontSize: 12 },
    },
    grid: { left: 48, right: useRightAxis ? 48 : 16, top: 40, bottom: 28 },
    xAxis: {
      type: 'time',
      axisLabel: { fontSize: 11, formatter: (v) => {
        const d = new Date(v)
        const pad = (n) => String(n).padStart(2, '0')
        return `${pad(d.getHours())}:${pad(d.getMinutes())}`
      } },
    },
    yAxis: [
      {
        type: 'value',
        name: props.yAxisLeftName,
        nameTextStyle: { fontSize: 11 },
        axisLabel: { fontSize: 11 },
        splitLine: { lineStyle: { type: 'dashed', color: '#ebeef5' } },
      },
      ...(useRightAxis
        ? [{
            type: 'value',
            name: props.yAxisRightName,
            nameTextStyle: { fontSize: 11 },
            axisLabel: { fontSize: 11 },
            splitLine: { show: false },
          }]
        : []),
    ],
    series: props.series.map((s) => ({
      name: s.name,
      type: 'line',
      smooth: true,
      showSymbol: false,
      yAxisIndex: s.yAxisIndex || 0,
      data: s.data || [],
      lineStyle: { width: 2 },
      areaStyle: s.area ? { opacity: 0.08 } : undefined,
    })),
  }
}

function renderChart() {
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value)
  }
  chart.setOption(buildOption(), true)
}

function handleResize() {
  chart?.resize()
}

watch(
  () => props.series,
  () => renderChart(),
  { deep: true }
)

onMounted(() => {
  renderChart()
  resizeObserver = new ResizeObserver(handleResize)
  resizeObserver.observe(chartRef.value)
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.metric-line-chart { width: 100%; }
</style>
