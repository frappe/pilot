<template>

  <div class="mx-auto">
    <!-- Header with time window selector -->
    <div class="flex justify-between items-start gap-3 mb-6">
      <div>
        <h1 class="font-semibold text-ink-gray-9 text-xl">Analytics</h1>
        <p class="mt-1 text-ink-gray-5 text-sm sm:hidden">System and app metrics.</p>
        <p class="mt-1 text-ink-gray-5 text-sm hidden sm:block">System and application metrics for this bench.</p>
      </div>
      <Dropdown :options="windowOptions" placement="bottom-end">
        <template #default="{ open }">
          <Button variant="outline" size="sm" :active="open">
            <template #prefix>
              <span v-if="!isHistorical" class="bg-surface-green-8 rounded-full size-1.5 animate-pulse" />
            </template>
            <template #suffix><span class="size-4 lucide-chevron-down" /></template>
            {{ windowLabel }}
          </Button>
        </template>
      </Dropdown>
    </div>

    <!-- Loading state -->
    <div v-if="pageLoading" class="flex justify-center h-[50vh]">
      <LoadingText />
    </div>

    <template v-else>
      <!-- Live stats bar: CPU / Memory / Storage -->
      <div v-if="liveStats" class="bg-surface-white mb-6 border rounded-lg border-outline-gray-2 overflow-hidden">
        <div class="flex sm:flex-row flex-col divide-outline-gray-2 sm:divide-x">
          <div class="flex-1 px-4 sm:px-5 py-3 sm:py-4">
            <div class="mb-2 text-ink-gray-6 text-sm">CPU</div>
            <div class="bg-surface-gray-2 mb-2 rounded-full h-1 overflow-hidden">
              <div class="bg-surface-gray-7 rounded-full h-full"
                :style="{ width: Math.min(liveStats.cpu_percent, 100) + '%' }" />
            </div>
            <div class="text-ink-gray-6 text-sm">{{ liveStats.cpu_percent.toFixed(1) }}% of {{ liveStats.cpu_count }}
              vCPUs</div>
          </div>
          <div class="flex-1 px-4 sm:px-5 py-3 sm:py-4 border-t sm:border-t-0 border-outline-gray-2">
            <div class="mb-2 text-ink-gray-6 text-sm">Memory</div>
            <div class="bg-surface-gray-2 mb-2 rounded-full h-1 overflow-hidden">
              <div class="bg-surface-gray-7 rounded-full h-full"
                :style="{ width: Math.min(liveStats.memory_percent, 100) + '%' }" />
            </div>
            <div class="text-ink-gray-6 text-sm">{{ formatBytes(liveStats.memory_used) }} of {{
              formatBytes(liveStats.memory_total) }}</div>
          </div>
          <div class="flex-1 px-4 sm:px-5 py-3 sm:py-4 border-t sm:border-t-0 border-outline-gray-2">
            <div class="mb-2 text-ink-gray-6 text-sm">Storage</div>
            <div class="bg-surface-gray-2 mb-2 rounded-full h-1 overflow-hidden">
              <div class="bg-surface-gray-7 rounded-full h-full"
                :style="{ width: Math.min(liveStats.disk_percent, 100) + '%' }" />
            </div>
            <div class="text-ink-gray-6 text-sm">{{ formatBytes(liveStats.disk_used) }} of {{
              formatBytes(liveStats.disk_total) }}</div>
          </div>
        </div>
      </div>

      <!-- Historical empty states -->
      <template v-if="isHistorical">
        <LoadingText v-if="historyLoading" />
        <ErrorMessage v-else-if="historyError" :message="historyError" />
        <div v-else-if="allEmpty" class="flex flex-col justify-center items-center gap-2 h-[50vh] text-center">
          <span class="size-10 text-ink-gray-3 lucide-chart-line" />
          <p class="font-medium text-ink-gray-7 text-sm">No data for the last {{ windowLabel }}</p>
          <p class="text-ink-gray-5 text-xs">Monitoring hasn't collected metrics in this range yet.</p>
        </div>
      </template>

      <!-- Charts grid -->
      <div v-if="showCharts" class="gap-4 grid grid-cols-1 sm:grid-cols-2 mb-6">
        <ChartCard v-for="chart in charts" :key="chart.title" :title="chart.title">
          <AxisChart :config="chart.config" class="w-full min-w-0 h-full min-h-[300px] px-2 sm:px-4 py-2" />
        </ChartCard>
      </div>

      <!-- WAF analytics (only renders when the WAF has logged activity) -->
      <WafAnalytics :window="activeWindow" />
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Button, Dropdown, LoadingText, ErrorMessage, AxisChart } from 'frappe-ui'
import ChartCard from '@/components/common/ChartCard.vue'
import WafAnalytics from '@/components/common/WafAnalytics.vue'
import { apiErrorMessage } from '@/api/client'
import { monitorApi } from '@/api/monitor'

const WINDOWS = [
  { key: 'live', label: 'Live' },
  { key: '30m', label: '30 minutes' },
  { key: '1h', label: '1 hour' },
  { key: '6h', label: '6 hours' },
  { key: '12h', label: '12 hours' },
  { key: '24h', label: '24 hours' },
  { key: '1w', label: '1 week' },
]
const WINDOW_SECONDS = { '30m': 1800, '1h': 3600, '6h': 21600, '12h': 43200, '24h': 86400, '1w': 604800 }
const TIME_GRAIN = { live: 'second', '30m': 'minute', '1h': 'minute', '6h': 'hour', '12h': 'hour', '24h': 'hour', '1w': 'day' }
const PALETTE = ['#2490ef', '#f59e0b', '#10b981', '#8b5cf6', '#ef4444', '#06b6d4', '#ec4899']
const LIVE_WINDOW_MS = 1800 * 1000

// Series names and colors
const CPU_SERIES = ['Busy System', 'Busy User', 'Busy IOWait', 'Busy IRQ', 'Busy Other']
const MEMORY_SERIES = ['Used', 'Cached + Buffers', 'Free', 'Swap Used']
const NETWORK_SERIES = ['Received', 'Sent']
const DISK_IO_SERIES = ['Read', 'Write']
const DISK_SERIES = 'Root Disk'

const CPU_COLORS = {
  'Busy User': '#2490ef', 'Busy System': '#f59e0b', 'Busy IOWait': '#ef4444',
  'Busy IRQ': '#8b5cf6', 'Busy Other': '#ec4899',
}
const MEMORY_COLORS = {
  Used: '#f59e0b', 'Cached + Buffers': '#2490ef', Free: '#10b981', 'Swap Used': '#ef4444',
}

// State

const activeWindow = ref('live')
const isHistorical = computed(() => activeWindow.value !== 'live')
const windowLabel = computed(() => WINDOWS.find(w => w.key === activeWindow.value)?.label ?? '')
const windowOptions = computed(() => WINDOWS.map(w => ({ label: w.label, onClick: () => setWindow(w.key) })))

// Live mode state
const stats = ref(null)
const liveHistory = ref([])
const liveNow = ref(Date.now())
const timeOffset = ref(0)

// Historical mode state
const system = ref({ earliest: null, points: [], memory_total_mb: null, storage: null })
const application = ref({ earliest: null, services: [], cpu: [], memory: [] })
const historyLoading = ref(false)
const historyError = ref('')
const historyNow = ref(Date.now())

// Time helpers

function serverTime() {
  return Date.now() + timeOffset.value
}

const axisMin = computed(() => historyNow.value - (WINDOW_SECONDS[activeWindow.value] || 3600) * 1000)
const axisMax = computed(() => historyNow.value)

function isPartial(earliest) {
  return earliest != null && earliest > axisMin.value + 1000
}

function humanizeSince(earliest) {
  if (earliest == null) return ''
  const seconds = Math.floor((historyNow.value - earliest) / 1000)
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

// Data loading

function setWindow(key) {
  activeWindow.value = key
  if (key === 'live') {
    liveHistory.value = []
    application.value = { earliest: null, services: [], cpu: [], memory: [] }
    seedLiveHistory()
    loadStats()
  } else {
    loadHistory(key)
  }
}

async function loadStats() {
  if (isHistorical.value) return
  try {
    const s = await monitorApi.stats()
    stats.value = s
    liveNow.value = serverTime()
    appendLivePoint(s)
    trimLiveHistory()
    await refreshAppData()
  } catch { }
}

function appendLivePoint(s) {
  const cpu = s.cpu_breakdown || {}
  const mem = s.memory_breakdown || {}
  const [load1, load5, load15] = s.load_avg || []
  const network = s.network || {}
  const diskIo = s.disk_io || {}
  liveHistory.value.push({
    time: serverTime(),
    'Busy User': cpu.user, 'Busy System': cpu.system, 'Busy IOWait': cpu.iowait,
    'Busy IRQ': cpu.irq, 'Busy Other': cpu.other,
    Used: mem.used_mb, 'Cached + Buffers': mem.cached_mb, Free: mem.free_mb, 'Swap Used': mem.swap_used_mb,
    Load1: load1, Load5: load5, Load15: load15,
    [DISK_SERIES]: s.disk_percent,
    Received: network.rx_bytes_per_sec, Sent: network.tx_bytes_per_sec,
    Read: diskIo.read_bytes_per_sec, Write: diskIo.write_bytes_per_sec,
  })
}

function trimLiveHistory() {
  const cutoff = liveNow.value - LIVE_WINDOW_MS - 60000
  liveHistory.value = liveHistory.value.filter(p => p.time >= cutoff)
}

async function refreshAppData() {
  try {
    const d = await monitorApi.history('30m')
    if (!d.error && d.application) {
      application.value = d.application
    }
  } catch { }
}

async function seedLiveHistory() {
  if (isHistorical.value || liveHistory.value.length) return
  try {
    const d = await monitorApi.history('1h')
    if (d.error) throw new Error(apiErrorMessage(d, 'Failed to load analytics.'))
    const serverNow = d.now ?? Date.now()
    timeOffset.value = serverNow - Date.now()
    if (d.system?.points?.length) {
      liveHistory.value = d.system.points
      liveNow.value = serverNow
    }
    if (d.application) {
      application.value = d.application
    }
  } catch { }
}

async function loadHistory(window) {
  historyLoading.value = true
  historyError.value = ''
  try {
    const d = await monitorApi.history(window)
    if (d.error) throw new Error(apiErrorMessage(d, 'Failed to load analytics.'))
    historyNow.value = d.now ?? Date.now()
    system.value = d.system
    application.value = d.application
  } catch (e) {
    historyError.value = e.message
  } finally {
    historyLoading.value = false
  }
}

// Derived state

const liveStats = computed(() => !isHistorical.value ? stats.value : null)
const systemEmpty = computed(() => isHistorical.value && !historyLoading.value && !system.value.points.length)
const appEmpty = computed(() => isHistorical.value && !historyLoading.value && !application.value.cpu.length)
const allEmpty = computed(() => isHistorical.value && !historyLoading.value && !historyError.value && systemEmpty.value && appEmpty.value)
const pageLoading = computed(() => isHistorical.value ? historyLoading.value : (!stats.value || liveHistory.value.length < 2))
const showCharts = computed(() => isHistorical.value
  ? (!historyLoading.value && !historyError.value && !systemEmpty.value)
  : liveHistory.value.length > 1)

// Chart helpers

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }

const fixedXAxis = computed(() => ({
  key: 'time',
  type: 'time',
  timeGrain: TIME_GRAIN[activeWindow.value],
  echartOptions: { min: axisMin.value, max: axisMax.value, splitLine: GRID },
}))

const liveXAxis = computed(() => ({
  key: 'time',
  type: 'time',
  timeGrain: 'second',
  echartOptions: { min: liveNow.value - LIVE_WINDOW_MS, max: liveNow.value, splitLine: GRID },
}))

const currentPoints = computed(() => isHistorical.value ? system.value.points : liveHistory.value)
const currentXAxis = computed(() => isHistorical.value ? fixedXAxis.value : liveXAxis.value)

function lineSeries(name, color, stacked) {
  return {
    name,
    type: 'line',
    color,
    echartOptions: {
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      showSymbol: false,
      stack: stacked ? 'total' : undefined,
      lineStyle: { width: 1.5 },
      areaStyle: { color: transparent(color, 0.25) },
      emphasis: { focus: 'series' },
    },
  }
}

function transparent(hex, opacity) {
  const v = parseInt(hex.slice(1), 16)
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${opacity})`
}

function scaleFields(points, keys, divisor) {
  return points.map(p => ({ ...p, ...Object.fromEntries(keys.map(k => [k, p[k] != null ? p[k] / divisor : p[k]])) }))
}

function normalizeAppData(points, services) {
  return points.map(p => ({ time: p.time, ...Object.fromEntries(services.map(s => [s, p[s] ?? 0])) }))
}

// Chart configs

const cpuChartConfig = computed(() => ({
  title: 'CPU',
  config: {
    data: currentPoints.value.map(p => ({
      time: p.time,
      'Busy User': p['Busy User'] ?? 0,
      'Busy System': p['Busy System'] ?? 0,
      'Busy IOWait': p['Busy IOWait'] ?? 0,
      'Busy IRQ': p['Busy IRQ'] ?? 0,
      'Busy Other': p['Busy Other'] ?? 0,
    })),
    xAxis: currentXAxis.value,
    yAxis: { yMin: 0, yMax: 100, echartOptions: { name: '%', splitLine: GRID } },
    series: CPU_SERIES.map(name => lineSeries(name, CPU_COLORS[name], true)),
  },
}))

const loadChartConfig = computed(() => ({
  title: 'Load Average',
  config: {
    data: currentPoints.value.map(p => ({
      time: p.time,
      'Load Average 1': p.Load1 ?? 0,
      'Load Average 5': p.Load5 ?? 0,
      'Load Average 15': p.Load15 ?? 0,
    })),
    xAxis: currentXAxis.value,
    yAxis: { yMin: 0, echartOptions: { name: '', splitLine: GRID } },
    series: [
      lineSeries('Load Average 1', '#46B37E'),
      lineSeries('Load Average 5', '#F2D14B'),
      lineSeries('Load Average 15', '#E03636'),
    ],
  },
}))

const memChartConfig = computed(() => {
  const data = scaleFields(currentPoints.value, MEMORY_SERIES, 1024)
  const peak = data.reduce((max, p) => Math.max(max, MEMORY_SERIES.reduce((sum, k) => sum + (p[k] || 0), 0)), 0)
  return {
    title: 'Memory',
    config: {
      data,
      xAxis: currentXAxis.value,
      yAxis: { yMin: 0, yMax: peak > 0 ? peak * 1.1 : undefined, echartOptions: { name: 'GB', splitLine: GRID } },
      series: MEMORY_SERIES.map(name => lineSeries(name, MEMORY_COLORS[name], true)),
    },
  }
})

const diskInfo = computed(() => isHistorical.value
  ? system.value.storage?.disk
  : (stats.value ? { used_mb: stats.value.disk_used / 1024 ** 2, total_mb: stats.value.disk_total / 1024 ** 2 } : null))

const diskChartConfig = computed(() => ({
  title: 'Disk',
  config: {
    data: currentPoints.value,
    xAxis: currentXAxis.value,
    yAxis: { yMin: 0, yMax: 100, echartOptions: { name: '%', splitLine: GRID } },
    series: [lineSeries(DISK_SERIES, PALETTE[0])],
  },
}))

const networkChartConfig = computed(() => ({
  title: 'Network',
  config: {
    data: scaleFields(currentPoints.value, NETWORK_SERIES, 1024 ** 2),
    xAxis: currentXAxis.value,
    yAxis: { yMin: 0, echartOptions: { name: 'MB/s', splitLine: GRID } },
    series: NETWORK_SERIES.map((name, i) => lineSeries(name, PALETTE[i])),
  },
}))

const diskIoChartConfig = computed(() => ({
  title: 'Disk I/O',
  config: {
    data: scaleFields(currentPoints.value, DISK_IO_SERIES, 1024 ** 2),
    xAxis: currentXAxis.value,
    yAxis: { yMin: 0, echartOptions: { name: 'MB/s', splitLine: GRID } },
    series: DISK_IO_SERIES.map((name, i) => lineSeries(name, PALETTE[i])),
  },
}))

const appWindowData = computed(() => {
  const data = application.value
  if (!data.services.length) return { services: [], cpu: [], memory: [] }
  if (isHistorical.value) return data
  const latest = data.cpu.length ? Math.max(...data.cpu.map(p => p.time)) : 0
  const cutoff = latest ? latest - LIVE_WINDOW_MS : 0
  return {
    services: data.services,
    cpu: cutoff ? data.cpu.filter(p => p.time >= cutoff) : data.cpu,
    memory: cutoff ? data.memory.filter(p => p.time >= cutoff) : data.memory,
  }
})

const appCpuConfig = computed(() => ({
  title: 'Process CPU',
  config: {
    data: normalizeAppData(appWindowData.value.cpu, appWindowData.value.services),
    xAxis: currentXAxis.value,
    yAxis: { yMin: 0, yMax: 100, echartOptions: { name: '%', splitLine: GRID } },
    series: appWindowData.value.services.map((name, i) => lineSeries(name, PALETTE[i])),
  },
}))

const appMemConfig = computed(() => ({
  title: 'Process Memory',
  config: {
    data: normalizeAppData(appWindowData.value.memory, appWindowData.value.services),
    xAxis: currentXAxis.value,
    yAxis: { yMin: 0, echartOptions: { name: 'MB', splitLine: GRID } },
    series: appWindowData.value.services.map((name, i) => lineSeries(name, PALETTE[i])),
  },
}))

// Combine all charts for template rendering
const charts = computed(() => [
  cpuChartConfig.value,
  loadChartConfig.value,
  memChartConfig.value,
  ...(diskInfo.value ? [diskChartConfig.value] : []),
  networkChartConfig.value,
  diskIoChartConfig.value,
  ...(appWindowData.value.cpu.length ? [appCpuConfig.value, appMemConfig.value] : []),
])

// Formatting

function formatBytes(bytes) {
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(0) + ' KB'
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return (bytes / 1024 ** 3).toFixed(1) + ' GB'
}

// Lifecycle

let statsTimer
onMounted(async () => {
  await seedLiveHistory()
  await loadStats()
  statsTimer = setInterval(loadStats, 10000)
})
onUnmounted(() => clearInterval(statsTimer))
</script>
