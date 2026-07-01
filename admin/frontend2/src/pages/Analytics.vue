<template>
  <UpdatesAvailableButton />

  <div class="mx-auto">
    <div class="flex justify-between items-start gap-3 mb-6">
      <div>
        <h1 class="font-semibold text-ink-gray-9 text-xl">Analytics</h1>
        <p class="mt-1 text-ink-gray-5 text-sm">System and application metrics for this bench.</p>
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

    <div v-if="pageLoading" class="flex justify-center h-[50vh]">
      <LoadingText />
    </div>

    <template v-else>
      <div v-if="!isHistorical && stats" class="bg-white mb-6 border rounded-lg border-outline-gray-1 overflow-hidden">
        <div class="flex sm:flex-row flex-col divide-outline-gray-2 sm:divide-x">
          <div class="flex-1 px-4 sm:px-5 py-3 sm:py-4">
            <div class="mb-2 text-ink-gray-6 text-sm">CPU</div>
            <div class="mb-2 rounded-full h-1 overflow-hidden" style="background: #f3f4f6;">
              <div class="rounded-full h-full" style="background: #171717;"
                :style="{ width: Math.min(stats.cpu_percent, 100) + '%' }" />
            </div>
            <div class="text-ink-gray-6 text-sm">{{ stats.cpu_percent.toFixed(1) }}% of {{ stats.cpu_count }} vCPUs
            </div>
          </div>
          <div class="flex-1 px-4 sm:px-5 py-3 sm:py-4 border-t sm:border-t-0 border-outline-gray-2">
            <div class="mb-2 text-ink-gray-6 text-sm">Memory</div>
            <div class="mb-2 rounded-full h-1 overflow-hidden" style="background: #f3f4f6;">
              <div class="rounded-full h-full" style="background: #171717;"
                :style="{ width: Math.min(stats.memory_percent, 100) + '%' }" />
            </div>
            <div class="text-ink-gray-6 text-sm">{{ formatBytes(stats.memory_used) }} of {{
              formatBytes(stats.memory_total) }}</div>
          </div>
          <div class="flex-1 px-4 sm:px-5 py-3 sm:py-4 border-t sm:border-t-0 border-outline-gray-2">
            <div class="mb-2 text-ink-gray-6 text-sm">Storage</div>
            <div class="mb-2 rounded-full h-1 overflow-hidden" style="background: #f3f4f6;">
              <div class="rounded-full h-full" style="background: #171717;"
                :style="{ width: Math.min(stats.disk_percent, 100) + '%' }" />
            </div>
            <div class="text-ink-gray-6 text-sm">{{ formatBytes(stats.disk_used) }} of {{ formatBytes(stats.disk_total)
            }}</div>
          </div>
        </div>
      </div>

      <template v-if="isHistorical">
        <LoadingText v-if="historyLoading" />
        <ErrorMessage v-else-if="historyError" :message="historyError" />
        <div v-else-if="allEmpty" class="flex flex-col justify-center items-center gap-2 h-[50vh] text-center">
          <span class="size-10 text-ink-gray-3 lucide-chart-line" />
          <p class="font-medium text-ink-gray-7 text-sm">No data for the last {{ windowLabel }}</p>
          <p class="text-ink-gray-5 text-xs">Monitoring hasn't collected metrics in this range yet.</p>
        </div>
      </template>

      <!-- Charts -->
      <div v-if="showCharts" class="gap-4 grid grid-cols-1 sm:grid-cols-2 mb-6">
        <ChartCard :title="cpuChartConfig.title">
          <AxisChart :config="cpuChartConfig.config" />
        </ChartCard>
        <ChartCard :title="loadChartConfig.title">
          <AxisChart :config="loadChartConfig.config" />
        </ChartCard>
        <ChartCard :title="memChartConfig.title">
          <AxisChart :config="memChartConfig.config" />
        </ChartCard>
        <ChartCard v-if="hasDisk" :title="diskChartConfig.title">
          <AxisChart :config="diskChartConfig.config" />
        </ChartCard>
        <ChartCard :title="networkChartConfig.title">
          <AxisChart :config="networkChartConfig.config" />
        </ChartCard>
        <ChartCard :title="diskIoChartConfig.title">
          <AxisChart :config="diskIoChartConfig.config" />
        </ChartCard>
      </div>

      <!-- Application (historical) -->
      <div v-if="isHistorical && !historyLoading && !historyError && !appEmpty"
        class="sm:bg-surface-white sm:shadow-sm sm:px-6 py-1 sm:py-5 sm:border sm:rounded-lg sm:border-outline-gray-1">
        <h2 class="mb-4 font-semibold text-ink-gray-9">Application Processes</h2>
        <div class="flex flex-col gap-4">
          <ChartCard :title="appCpuConfig.title">
            <AxisChart :config="appCpuConfig.config" class="p-0" />
          </ChartCard>
          <ChartCard :title="appMemConfig.title">
            <AxisChart :config="appMemConfig.config" />
          </ChartCard>
        </div>
      </div>
    </template>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Button, Dropdown, LoadingText, ErrorMessage, AxisChart } from 'frappe-ui'
import UpdatesAvailableButton from '@/components/UpdatesAvailableButton.vue'
import ChartCard from '@/components/ChartCard.vue'
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

const activeWindow = ref('live')
const isHistorical = computed(() => activeWindow.value !== 'live')
const windowLabel = computed(() => WINDOWS.find(w => w.key === activeWindow.value)?.label ?? '')
const windowOptions = computed(() => WINDOWS.map(w => ({ label: w.label, onClick: () => setWindow(w.key) })))

function setWindow(key) {
  activeWindow.value = key
  if (key === 'live') {
    liveNow.value = Date.now()
    seedLiveHistory()
    loadStats()
  } else {
    loadHistory(key)
  }
}

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

const stats = ref(null)
const liveHistory = ref([])
const MAX_LIVE = 60
const LIVE_WINDOW_MS = 900 * 1000
const liveNow = ref(Date.now())

async function loadStats() {
  if (isHistorical.value) return
  try {
    const s = await monitorApi.stats()
    stats.value = s
    liveNow.value = Date.now()
    const cpu = s.cpu_breakdown || {}
    const mem = s.memory_breakdown || {}
    const [load1, load5, load15] = s.load_avg || []
    const network = s.network || {}
    const diskIo = s.disk_io || {}
    liveHistory.value = [
      ...liveHistory.value.slice(-(MAX_LIVE - 1)),
      {
        time: new Date(),
        'Busy User': cpu.user, 'Busy System': cpu.system, 'Busy IOWait': cpu.iowait,
        'Busy IRQ': cpu.irq, 'Busy Other': cpu.other,
        Used: mem.used_mb, 'Cached + Buffers': mem.cached_mb, Free: mem.free_mb, 'Swap Used': mem.swap_used_mb,
        Load1: load1, Load5: load5, Load15: load15,
        [DISK_SERIES]: s.disk_percent,
        Received: network.rx_bytes_per_sec, Sent: network.tx_bytes_per_sec,
        Read: diskIo.read_bytes_per_sec, Write: diskIo.write_bytes_per_sec,
      },
    ]
  } catch { }
}

async function seedLiveHistory() {
  if (isHistorical.value || liveHistory.value.length) return
  try {
    const d = await monitorApi.history('30m')
    if (d.error) throw new Error(d.error)
    if (d.system?.points?.length) {
      liveHistory.value = d.system.points
      liveNow.value = d.now ?? Date.now()
    }
  } catch { }
}

const system = ref({ earliest: null, points: [], memory_total_mb: null, storage: null })
const application = ref({ earliest: null, services: [], cpu: [], memory: [] })
const historyLoading = ref(false)
const historyError = ref('')
const historyNow = ref(Date.now())

async function loadHistory(window) {
  historyLoading.value = true
  historyError.value = ''
  try {
    const d = await monitorApi.history(window)
    if (d.error) throw new Error(d.error)
    historyNow.value = d.now ?? Date.now()
    system.value = d.system
    application.value = d.application
  } catch (e) {
    historyError.value = e.message
  } finally {
    historyLoading.value = false
  }
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

const systemEmpty = computed(() => isHistorical.value && !historyLoading.value && !system.value.points.length)
const appEmpty = computed(() => isHistorical.value && !historyLoading.value && !application.value.cpu.length)
const allEmpty = computed(() => isHistorical.value && !historyLoading.value && !historyError.value && systemEmpty.value && appEmpty.value)

function transparent(hex, opacity) {
  const v = parseInt(hex.slice(1), 16)
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${opacity})`
}

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

function scaleFields(points, keys, divisor) {
  return points.map(p => ({ ...p, ...Object.fromEntries(keys.map(k => [k, p[k] != null ? p[k] / divisor : p[k]])) }))
}

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

const cpuChartConfig = computed(() => ({
  title: 'CPU',
  config: {
    data: currentPoints.value,
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
const hasDisk = computed(() => diskInfo.value != null)

const diskChartConfig = computed(() => {
  const disk = diskInfo.value
  return {
    title: 'Disk',
    config: {
      data: currentPoints.value,
      xAxis: currentXAxis.value,
      yAxis: { yMin: 0, yMax: 100, echartOptions: { name: '%', splitLine: GRID } },
      series: [lineSeries(DISK_SERIES, PALETTE[0])],
    },
  }
})

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

const appCpuConfig = computed(() => ({
  title: 'Process CPU',
  config: {
    data: application.value.cpu,
    xAxis: fixedXAxis.value,
    yAxis: { yMin: 0, yMax: 100, echartOptions: { name: '%', splitLine: GRID } },
    series: application.value.services.map((name, i) => lineSeries(name, PALETTE[i])),
  },
}))

const appMemConfig = computed(() => ({
  title: 'Process Memory',
  config: {
    data: application.value.memory,
    xAxis: fixedXAxis.value,
    yAxis: { yMin: 0, echartOptions: { name: 'MB', splitLine: GRID } },
    series: application.value.services.map((name, i) => lineSeries(name, PALETTE[i])),
  },
}))

const pageLoading = computed(() => isHistorical.value ? historyLoading.value : (!stats.value || liveHistory.value.length < 2))

const showCharts = computed(() => isHistorical.value
  ? (!historyLoading.value && !historyError.value && !systemEmpty.value)
  : liveHistory.value.length > 1)

function formatBytes(bytes) {
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(0) + ' KB'
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return (bytes / 1024 ** 3).toFixed(1) + ' GB'
}

let statsTimer
onMounted(async () => {
  await seedLiveHistory()
  await loadStats()
  statsTimer = setInterval(loadStats, 3000)
})
onUnmounted(() => clearInterval(statsTimer))
</script>
