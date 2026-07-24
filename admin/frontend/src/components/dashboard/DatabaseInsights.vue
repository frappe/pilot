<template>
  <div>
    <div v-if="loading" class="flex justify-center h-[40vh]">
      <LoadingText />
    </div>
    <ErrorMessage v-else-if="error" :message="error" />
    <div
      v-else-if="unsupported"
      class="flex flex-col justify-center items-center gap-2 h-[40vh] text-center"
    >
      <span class="size-10 text-ink-gray-3 lucide-database" />
      <p class="font-medium text-ink-gray-7 text-sm">DB analyzer supports MariaDB only</p>
    </div>
    <div
      v-else-if="empty"
      class="flex flex-col justify-center items-center gap-2 h-[40vh] text-center"
    >
      <span class="size-10 text-ink-gray-3 lucide-database" />
      <p class="font-medium text-ink-gray-7 text-sm">No data for the selected range</p>
      <p class="text-ink-gray-5 text-xs">
        The monitor hasn't sampled the database in this range yet.
      </p>
    </div>

    <div v-else class="gap-4 grid grid-cols-1 sm:grid-cols-2">
      <ChartCard v-for="chart in charts" :key="chart.title" :title="chart.title">
        <AxisChart
          :config="chart.config"
          class="w-full min-w-0 h-full min-h-[300px] px-2 sm:px-4 py-2"
        />
      </ChartCard>
      <SlowQueries v-if="!unsupported" :overview="data?.slow_queries" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { AxisChart, ErrorMessage, LoadingText } from 'frappe-ui'
import ChartCard from '@/components/common/ChartCard.vue'
import SlowQueries from '@/components/dashboard/SlowQueries.vue'
import { apiErrorMessage } from '@/api/client'
import { monitorApi } from '@/api/monitor'
import { formatBytes } from '@/utils/format'

const props = defineProps({ window: { type: String, default: '1h' } })

const TIME_GRAIN = {
  '30m': 'minute',
  '1h': 'minute',
  '6h': 'hour',
  '12h': 'hour',
  '24h': 'hour',
  '1w': 'day',
}
const PALETTE = ['#2490ef', '#f59e0b', '#10b981', '#8b5cf6', '#ef4444', '#06b6d4']
const QUERY_SERIES = ['Insert', 'Update', 'Delete', 'Select', 'Other']

const loading = ref(true)
const error = ref('')
const data = ref(null)

const points = computed(() => data.value?.points ?? [])
const unsupported = computed(() => data.value?.slow_queries?.unsupported === true)
const empty = computed(() => !unsupported.value && points.value.length === 0)

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }

const xAxis = computed(() => ({
  key: 'time',
  type: 'time',
  timeGrain: TIME_GRAIN[props.window] ?? 'minute',
  echartOptions: {
    min: (data.value?.now ?? Date.now()) - (data.value?.window_seconds ?? 3600) * 1000,
    max: data.value?.now ?? Date.now(),
    splitLine: GRID,
  },
}))

function areaSeries(name, color) {
  return {
    name,
    type: 'line',
    color,
    echartOptions: {
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      showSymbol: false,
      lineStyle: { width: 1.5 },
      areaStyle: { color: transparent(color, 0.2) },
      emphasis: { focus: 'series' },
    },
  }
}

function transparent(hex, opacity) {
  const v = parseInt(hex.slice(1), 16)
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${opacity})`
}

// Full formatter (frappe-ui overrides valueFormatter) so bytes read as GB/MB.
function bytesTooltip(paramsInput) {
  const params = (Array.isArray(paramsInput) ? paramsInput : [paramsInput]).filter(
    (p) => p.value?.[1] != null,
  )
  if (!params.length) return ''
  const date = new Date(params[0].value[0]).toLocaleString()
  const rows = params
    .map(
      (p) =>
        `<div class="flex items-center gap-2">${p.marker}<span class="flex-1">${p.seriesName}</span><b>${formatBytes(p.value[1])}</b></div>`,
    )
    .join('')
  return `<div>${date}${rows}</div>`
}

const bytesAxis = {
  yMin: 0,
  echartOptions: {
    name: 'bytes',
    axisLabel: { formatter: (v) => formatBytes(v) },
    splitLine: GRID,
  },
}

// frappe-ui only auto-shows the legend when a chart has more than one series; force it on for all charts.
const LEGEND_OPTIONS = { legend: { show: true }, grid: { bottom: 40 } }

const charts = computed(() => [
  {
    title: 'Queries',
    config: {
      data: points.value,
      xAxis: xAxis.value,
      yAxis: { yMin: 0, echartOptions: { name: 'count', splitLine: GRID } },
      series: QUERY_SERIES.map((name, i) => areaSeries(name, PALETTE[i])),
      echartOptions: LEGEND_OPTIONS,
    },
  },
  {
    title: 'DB connections',
    config: {
      data: points.value,
      xAxis: xAxis.value,
      yAxis: { yMin: 0, echartOptions: { name: 'connections', splitLine: GRID } },
      series: [areaSeries('Connected', PALETTE[0]), areaSeries('Max Connections', PALETTE[2])],
      echartOptions: LEGEND_OPTIONS,
    },
  },
  {
    title: 'Average row lock time (ms)',
    config: {
      data: points.value,
      xAxis: xAxis.value,
      yAxis: { yMin: 0, echartOptions: { name: 'ms', splitLine: GRID } },
      series: [areaSeries('Avg Row Lock Wait', PALETTE[3])],
    },
  },
  {
    title: 'Buffer pool size',
    config: {
      data: points.value,
      xAxis: xAxis.value,
      yAxis: bytesAxis,
      series: [areaSeries('Buffer Pool Size', PALETTE[5])],
      echartOptions: { tooltip: { formatter: bytesTooltip } },
    },
  },
  {
    title: 'Buffer pool size of total RAM',
    config: {
      data: points.value,
      xAxis: xAxis.value,
      yAxis: { yMin: 0, yMax: 100, echartOptions: { name: '%', splitLine: GRID } },
      series: [bufferPoolRamSeries()],
    },
  },
  {
    title: 'Buffer pool miss percent',
    config: {
      data: points.value,
      xAxis: xAxis.value,
      yAxis: { yMin: 0, echartOptions: { name: '%', splitLine: GRID } },
      series: [bufferPoolMissSeries()],
    },
  },
])

function thresholdMarkLine(entries) {
  return {
    symbol: 'none',
    silent: true,
    lineStyle: { color: '#ef4444', type: 'dashed' },
    label: { formatter: (p) => p.data.name, color: 'var(--ink-gray-6)', position: 'insideEndTop' },
    data: entries.map(([yAxis, name]) => ({ yAxis, name })),
  }
}

// InnoDB buffer pool as a % of RAM, with the standard too-low / too-high guides.
function bufferPoolRamSeries() {
  const series = areaSeries('Buffer Pool % RAM', PALETTE[0])
  series.echartOptions.markLine = thresholdMarkLine([
    [65, 'Too High InnoDB Buffer Pool (65%)'],
    [15, 'Too Low InnoDB Buffer Pool (15%)'],
  ])
  return series
}

// InnoDB buffer pool miss rate, with the standard too-high guide.
function bufferPoolMissSeries() {
  const series = areaSeries('Buffer Pool Miss %', PALETTE[1])
  series.echartOptions.markLine = thresholdMarkLine([[1, 'Too High Buffer Pool Miss (1%)']])
  return series
}

// Out-of-order window switches: only the latest load writes state.
let loadGeneration = 0

async function load() {
  const generation = ++loadGeneration
  if (!data.value) loading.value = true
  error.value = ''
  try {
    const result = await monitorApi.dbHistory(props.window)
    if (generation !== loadGeneration) return
    if (result.error) throw new Error(apiErrorMessage(result, 'Could not load database metrics.'))
    data.value = result
  } catch (e) {
    if (generation !== loadGeneration) return
    error.value = e.message || 'Could not load database metrics.'
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

// Reset on window change so the spinner shows for the new range.
watch(
  () => props.window,
  () => {
    data.value = null
    load()
  },
)

// Daemon samples every ~10s; a 5-minute refresh keeps charts current.
let refreshTimer
onMounted(() => {
  load()
  refreshTimer = setInterval(load, 300000)
})
onUnmounted(() => clearInterval(refreshTimer))
</script>
