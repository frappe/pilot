<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ErrorMessage, Select, Skeleton } from 'frappe-ui'
import { AreaChart } from 'frappe-ui/charts'

import ChartCard from '@/components/common/ChartCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import StickyToolbar from '@/components/common/StickyToolbar.vue'
import WafAnalytics from '@/components/common/WafAnalytics.vue'
import DatabaseInsights from '@/components/dashboard/DatabaseInsights.vue'
import SiteInsights from '@/components/dashboard/SiteInsights.vue'

import { apiErrorMessage } from '@/api/client'
import { monitorApi } from '@/api/monitor'
import { livePollDelayMs } from '@/utils/livePolling'
import { useSites } from '@/composables/sites/useSites'
import { useIsMobile } from '@/composables/common/useIsMobile'

const WINDOWS = [
  { key: 'live', label: 'Live' },
  { key: '30m', label: '30 minutes' },
  { key: '1h', label: '1 hour' },
  { key: '6h', label: '6 hours' },
  { key: '12h', label: '12 hours' },
  { key: '24h', label: '24 hours' },
  { key: '1w', label: '1 week' },
]
const WINDOW_SECONDS = {
  '30m': 1800,
  '1h': 3600,
  '6h': 21600,
  '12h': 43200,
  '24h': 86400,
  '1w': 604800,
}
const TIME_GRAIN = {
  live: 'second',
  '30m': 'minute',
  '1h': 'minute',
  '6h': 'hour',
  '12h': 'hour',
  '24h': 'hour',
  '1w': 'day',
}
const PALETTE = [
  'var(--ink-blue-5)',
  'var(--ink-amber-5)',
  'var(--ink-green-5)',
  'var(--ink-purple-5)',
  'var(--ink-red-5)',
  'var(--ink-cyan-5)',
  'var(--ink-pink-5)',
]
const LIVE_WINDOW_MS = 1800 * 1000

// Series names and colors
const CPU_SERIES = ['Busy System', 'Busy User', 'Busy IOWait', 'Busy IRQ', 'Busy Other']
const MEMORY_SERIES = ['Used', 'Cached + Buffers', 'Free', 'Swap Used']
const NETWORK_SERIES = ['Received', 'Sent']
const DISK_IO_SERIES = ['Read', 'Write']
const DISK_SERIES = 'Root Disk'

const CPU_COLORS = {
  'Busy User': 'var(--ink-blue-5)',
  'Busy System': 'var(--ink-amber-5)',
  'Busy IOWait': 'var(--ink-red-5)',
  'Busy IRQ': 'var(--ink-purple-5)',
  'Busy Other': 'var(--ink-pink-5)',
}
const MEMORY_COLORS = {
  Used: 'var(--ink-amber-5)',
  'Cached + Buffers': 'var(--ink-blue-5)',
  Free: 'var(--ink-green-5)',
  'Swap Used': 'var(--ink-red-5)',
}

// State

const route = useRoute()
const router = useRouter()

const VIEWS = [
  { key: 'system', label: 'System' },
  { key: 'database', label: 'Database' },
  { key: 'site', label: 'Site' },
]
const VIEW_KEYS = VIEWS.map((v) => v.key)
const WINDOW_KEYS = WINDOWS.map((w) => w.key)

const view = ref(VIEW_KEYS.includes(route.query.view) ? route.query.view : 'system')
const initialWindow = WINDOW_KEYS.includes(route.query.window) ? route.query.window : 'live'
const activeWindow = ref(view.value !== 'system' && initialWindow === 'live' ? '1h' : initialWindow)
const isHistorical = computed(() => activeWindow.value !== 'live')

const isServerScope = computed(() => view.value !== 'site')

// Target, not metric: returning to the server keeps the metric on show. A
// site has none of its own, so it has to land on System.
const targetOptions = computed(() => [
  { label: 'Server', value: 'server', icon: 'lucide-server' },
  ...sites.value.map((site) => ({ label: site.name, value: site.name, icon: 'lucide-globe' })),
])
const target = computed({
  get: () => (view.value === 'site' ? activeSite.value : 'server'),
  set: (value) =>
    value === 'server' ? setView(isServerScope.value ? view.value : 'system') : selectSite(value),
})

// Server-level metrics only; a site has one chart set and needs no picker.
const metricOptions = VIEWS.filter((v) => v.key !== 'site').map((v) => ({
  label: v.label,
  value: v.key,
}))
const metric = computed({ get: () => view.value, set: (value) => setView(value) })

// Only the system view has a live mode, so hide it elsewhere.
const windowLabel = computed(() => WINDOWS.find((w) => w.key === activeWindow.value)?.label ?? '')
const windowOptions = computed(() =>
  WINDOWS.filter((w) => view.value === 'system' || w.key !== 'live').map((w) => ({
    label: w.label,
    value: w.key,
  })),
)
const windowModel = computed({ get: () => activeWindow.value, set: (value) => chooseWindow(value) })
// Database and site charts never receive 'live'.
const historyWindow = computed(() => (activeWindow.value === 'live' ? '1h' : activeWindow.value))

// Only the system view offers live, so leaving forces an hour; coming back
// restores what the viewer actually picked.
let preferredWindow = activeWindow.value

const chooseWindow = (key) => {
  preferredWindow = key
  selectWindow(key)
}

const setView = (key) => {
  // Re-picking would refetch and, in live mode, discard collected history.
  if (view.value === key) return
  view.value = key
  if (key === 'system') setWindow(preferredWindow)
  else if (activeWindow.value === 'live') selectWindow('1h')
}

const selectSite = (name) => {
  setView('site')
  activeSite.value = name
}

const selectWindow = (key) => {
  if (view.value === 'system') setWindow(key)
  else activeWindow.value = key
}

// Site view
const { sites, loading: sitesLoading, load: loadSites } = useSites()
const isMobile = useIsMobile()
const activeSite = ref(typeof route.query.site === 'string' ? route.query.site : '')

// Persist view + window + site so a reload restores the same chart.
watch([view, activeWindow, activeSite], () => {
  router.replace({
    query: {
      view: view.value,
      window: activeWindow.value,
      ...(view.value === 'site' && activeSite.value ? { site: activeSite.value } : {}),
    },
  })
})

watch(
  view,
  async (value) => {
    if (!sites.value.length) await loadSites()
    if (value === 'site' && !sites.value.some((site) => site.name === activeSite.value)) {
      activeSite.value = sites.value[0]?.name ?? ''
    }
  },
  { immediate: true },
)

// Live mode state
const stats = ref(null)
const liveHistory = ref([])
const liveNow = ref(Date.now())
const timeOffset = ref(0)

// Historical mode state
const system = ref({ earliest: null, points: [], memory_total_bytes: null, storage: null })
const application = ref({ earliest: null, services: [], cpu: [], memory: [] })
const historyLoading = ref(false)
const historyError = ref('')
const historyNow = ref(Date.now())

// Time helpers

const serverTime = () => {
  return Date.now() + timeOffset.value
}

const axisMin = computed(
  () => historyNow.value - (WINDOW_SECONDS[activeWindow.value] || 3600) * 1000,
)
const axisMax = computed(() => historyNow.value)

const isPartial = (earliest) => {
  return earliest != null && earliest > axisMin.value + 1000
}

const humanizeSince = (earliest) => {
  if (earliest == null) return ''
  const seconds = Math.floor((historyNow.value - earliest) / 1000)
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

// Data loading

const setWindow = async (key) => {
  activeWindow.value = key
  if (key === 'live') {
    liveHistory.value = []
    application.value = { earliest: null, services: [], cpu: [], memory: [] }
    await seedLiveHistory()
    await loadStats()
    scheduleStats()
  } else {
    loadHistory(key)
  }
}

const loadStats = async () => {
  if (view.value !== 'system' || isHistorical.value) return
  try {
    const s = await monitorApi.stats()
    stats.value = s
    liveNow.value = serverTime()
    appendLivePoint(s)
    trimLiveHistory()
    await refreshAppData()
  } catch {}
}

const appendLivePoint = (s) => {
  const cpu = s.cpu_breakdown || {}
  const mem = s.memory_breakdown || {}
  const [load1, load5, load15] = s.load_avg || []
  const network = s.network || {}
  const diskIo = s.disk_io || {}
  liveHistory.value.push({
    time: serverTime(),
    'Busy User': cpu.user,
    'Busy System': cpu.system,
    'Busy IOWait': cpu.iowait,
    'Busy IRQ': cpu.irq,
    'Busy Other': cpu.other,
    Used: mem.used_bytes,
    'Cached + Buffers': mem.cached_bytes,
    Free: mem.free_bytes,
    'Swap Used': mem.swap_used_bytes,
    Load1: load1,
    Load5: load5,
    Load15: load15,
    [DISK_SERIES]: s.disk_percent,
    Received: network.rx_bytes_per_sec,
    Sent: network.tx_bytes_per_sec,
    Read: diskIo.read_bytes_per_sec,
    Write: diskIo.write_bytes_per_sec,
  })
}

const trimLiveHistory = () => {
  const cutoff = liveNow.value - LIVE_WINDOW_MS - 60000
  liveHistory.value = liveHistory.value.filter((p) => p.time >= cutoff)
}

const refreshAppData = async () => {
  try {
    const d = await monitorApi.history('30m')
    if (!d.error && d.application) {
      application.value = d.application
    }
  } catch {}
}

const seedLiveHistory = async () => {
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
  } catch {}
}

const loadHistory = async (window) => {
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

const liveStats = computed(() => (!isHistorical.value ? stats.value : null))

const liveMeters = computed(() => {
  const s = liveStats.value
  if (!s) return []
  return [
    {
      label: 'CPU',
      percent: s.cpu_percent,
      value: `${s.cpu_percent.toFixed(1)}% of ${s.cpu_count} vCPUs`,
    },
    {
      label: 'Memory',
      percent: s.memory_percent,
      value: `${formatBytes(s.memory_used)} of ${formatBytes(s.memory_total)}`,
    },
    {
      label: 'Storage',
      percent: s.disk_percent,
      value: `${formatBytes(s.disk_used)} of ${formatBytes(s.disk_total)}`,
    },
  ]
})
const systemEmpty = computed(
  () => isHistorical.value && !historyLoading.value && !system.value.points.length,
)
const appEmpty = computed(
  () => isHistorical.value && !historyLoading.value && !application.value.cpu.length,
)
const allEmpty = computed(
  () =>
    isHistorical.value &&
    !historyLoading.value &&
    !historyError.value &&
    systemEmpty.value &&
    appEmpty.value,
)
const showCharts = computed(() =>
  isHistorical.value
    ? !historyLoading.value && !historyError.value && !systemEmpty.value
    : liveHistory.value.length > 1,
)

// Chart helpers

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }
const fixedXAxis = computed(() => ({
  type: 'time',
  timeGrain: TIME_GRAIN[activeWindow.value],
  echartOptions: { min: axisMin.value, max: axisMax.value, splitLine: GRID },
}))

const liveXAxis = computed(() => ({
  type: 'time',
  timeGrain: 'second',
  echartOptions: { min: liveNow.value - LIVE_WINDOW_MS, max: liveNow.value, splitLine: GRID },
}))

const currentPoints = computed(() => (isHistorical.value ? system.value.points : liveHistory.value))
const currentXAxis = computed(() => (isHistorical.value ? fixedXAxis.value : liveXAxis.value))

const lineSeries = (color) => ({
  color,
  smooth: true,
  lineWidth: 1.5,
  showDataPoints: false,
  fillOpacity: 0.25,
})

const styleFor = (names, colorAt) =>
  Object.fromEntries(names.map((name, i) => [name, lineSeries(colorAt(name, i))]))

const scaleFields = (points, keys, divisor) => {
  return points.map((p) => ({
    ...p,
    ...Object.fromEntries(keys.map((k) => [k, p[k] != null ? p[k] / divisor : p[k]])),
  }))
}

const normalizeAppData = (points, services) => {
  return points.map((p) => ({
    time: p.time,
    ...Object.fromEntries(services.map((s) => [s, p[s] ?? 0])),
  }))
}

// Chart configs

const cpuChartConfig = computed(() => ({
  title: 'CPU',
  config: {
    data: currentPoints.value.map((p) => ({
      time: p.time,
      'Busy User': p['Busy User'] ?? 0,
      'Busy System': p['Busy System'] ?? 0,
      'Busy IOWait': p['Busy IOWait'] ?? 0,
      'Busy IRQ': p['Busy IRQ'] ?? 0,
      'Busy Other': p['Busy Other'] ?? 0,
    })),
    xAxis: currentXAxis.value,
    yAxis: { min: 0, max: 100, echartOptions: { name: '%', splitLine: GRID } },
    x: 'time',
    y: CPU_SERIES,
    stacked: true,
    seriesConfig: styleFor(CPU_SERIES, (name) => CPU_COLORS[name]),
  },
}))

const loadChartConfig = computed(() => ({
  title: 'Load Average',
  config: {
    data: currentPoints.value.map((p) => ({
      time: p.time,
      'Load Average 1': p.Load1 ?? 0,
      'Load Average 5': p.Load5 ?? 0,
      'Load Average 15': p.Load15 ?? 0,
    })),
    xAxis: currentXAxis.value,
    yAxis: { min: 0, echartOptions: { name: '', splitLine: GRID } },
    x: 'time',
    y: ['Load Average 1', 'Load Average 5', 'Load Average 15'],
    seriesConfig: {
      'Load Average 1': lineSeries('var(--ink-green-5)'),
      'Load Average 5': lineSeries('var(--ink-yellow-5)'),
      'Load Average 15': lineSeries('var(--ink-red-5)'),
    },
  },
}))

const memChartConfig = computed(() => {
  const data = scaleFields(currentPoints.value, MEMORY_SERIES, 1024 ** 3)
  const peak = data.reduce(
    (max, p) =>
      Math.max(
        max,
        MEMORY_SERIES.reduce((sum, k) => sum + (p[k] || 0), 0),
      ),
    0,
  )
  return {
    title: 'Memory',
    config: {
      data,
      xAxis: currentXAxis.value,
      yAxis: {
        yMin: 0,
        yMax: peak > 0 ? peak * 1.1 : undefined,
        echartOptions: { name: 'GB', splitLine: GRID },
      },
      x: 'time',
      y: MEMORY_SERIES,
      stacked: true,
      seriesConfig: styleFor(MEMORY_SERIES, (name) => MEMORY_COLORS[name]),
    },
  }
})

const diskInfo = computed(() =>
  isHistorical.value
    ? system.value.storage?.disk
    : stats.value
      ? { used_bytes: stats.value.disk_used, total_bytes: stats.value.disk_total }
      : null,
)

const diskChartConfig = computed(() => ({
  title: 'Disk',
  config: {
    data: currentPoints.value,
    xAxis: currentXAxis.value,
    yAxis: { min: 0, max: 100, echartOptions: { name: '%', splitLine: GRID } },
    x: 'time',
    y: DISK_SERIES,
    seriesConfig: { [DISK_SERIES]: lineSeries(PALETTE[0]) },
  },
}))

const networkChartConfig = computed(() => ({
  title: 'Network',
  config: {
    data: scaleFields(currentPoints.value, NETWORK_SERIES, 1024 ** 2),
    xAxis: currentXAxis.value,
    yAxis: { min: 0, echartOptions: { name: 'MB/s', splitLine: GRID } },
    x: 'time',
    y: NETWORK_SERIES,
    seriesConfig: styleFor(NETWORK_SERIES, (_, i) => PALETTE[i]),
  },
}))

const diskIoChartConfig = computed(() => ({
  title: 'Disk I/O',
  config: {
    data: scaleFields(currentPoints.value, DISK_IO_SERIES, 1024 ** 2),
    xAxis: currentXAxis.value,
    yAxis: { min: 0, echartOptions: { name: 'MB/s', splitLine: GRID } },
    x: 'time',
    y: DISK_IO_SERIES,
    seriesConfig: styleFor(DISK_IO_SERIES, (_, i) => PALETTE[i]),
  },
}))

const appWindowData = computed(() => {
  const data = application.value
  if (!data.services.length) return { services: [], cpu: [], memory: [] }
  if (isHistorical.value) return data
  const latest = data.cpu.length ? Math.max(...data.cpu.map((p) => p.time)) : 0
  const cutoff = latest ? latest - LIVE_WINDOW_MS : 0
  return {
    services: data.services,
    cpu: cutoff ? data.cpu.filter((p) => p.time >= cutoff) : data.cpu,
    memory: cutoff ? data.memory.filter((p) => p.time >= cutoff) : data.memory,
  }
})

const appCpuConfig = computed(() => ({
  title: 'Process CPU',
  config: {
    data: normalizeAppData(appWindowData.value.cpu, appWindowData.value.services),
    xAxis: currentXAxis.value,
    yAxis: { min: 0, max: 100, echartOptions: { name: '%', splitLine: GRID } },
    x: 'time',
    y: appWindowData.value.services,
    seriesConfig: styleFor(appWindowData.value.services, (_, i) => PALETTE[i]),
  },
}))

const appMemConfig = computed(() => ({
  title: 'Process Memory',
  config: {
    data: scaleFields(
      normalizeAppData(appWindowData.value.memory, appWindowData.value.services),
      appWindowData.value.services,
      1024 ** 2,
    ),
    xAxis: currentXAxis.value,
    yAxis: { min: 0, echartOptions: { name: 'MB', splitLine: GRID } },
    x: 'time',
    y: appWindowData.value.services,
    seriesConfig: styleFor(appWindowData.value.services, (_, i) => PALETTE[i]),
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

const formatBytes = (bytes) => {
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(0) + ' KB'
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return (bytes / 1024 ** 3).toFixed(1) + ' GB'
}

// Lifecycle

let statsTimer
const scheduleStats = () => {
  clearTimeout(statsTimer)
  const delay = livePollDelayMs({
    isLive: view.value === 'system' && !isHistorical.value,
    pointCount: liveHistory.value.length,
  })
  statsTimer = setTimeout(async () => {
    await loadStats()
    scheduleStats()
  }, delay)
}

onMounted(async () => {
  if (view.value === 'system') {
    if (isHistorical.value) loadHistory(activeWindow.value)
    else {
      // Seed first: monitor history reaches a drawable chart in one shot.
      await seedLiveHistory()
      await loadStats()
    }
  }
  scheduleStats()
})
onUnmounted(() => clearTimeout(statsTimer))
</script>

<template>
  <div class="px-4 pb-4">
    <StickyToolbar class="flex items-center gap-2">
      <Select
        v-model="target"
        :options="targetOptions"
        :size="isMobile ? 'md' : 'sm'"
        side="bottom"
        align="start"
        placeholder="Select site"
        class="flex-1 sm:flex-none min-w-0 sm:max-w-[250px]"
      />

      <Select
        v-if="isServerScope"
        v-model="metric"
        :options="metricOptions"
        :size="isMobile ? 'md' : 'sm'"
        side="bottom"
        align="start"
      />

      <Select
        v-model="windowModel"
        :options="windowOptions"
        :size="isMobile ? 'md' : 'sm'"
        side="bottom"
        align="start"
      >
        <template #prefix>
          <span
            v-if="!isHistorical"
            class="bg-surface-green-8 rounded-full size-1.5 animate-pulse"
          />
        </template>
      </Select>
    </StickyToolbar>

    <DatabaseInsights v-if="view === 'database'" :window="historyWindow" />

    <template v-else-if="view === 'site'">
      <SiteInsights v-if="activeSite" :site-name="activeSite" :window="historyWindow" />
      <EmptyState
        v-else-if="!sitesLoading"
        icon="lucide-globe"
        title="No sites on this bench yet"
        description="Create a site to start collecting metrics for it."
      />
    </template>

    <template v-else>
      <Skeleton v-if="!isHistorical && !liveStats" class="mb-4 rounded-6 h-[88px]" />

      <!-- Live stats bar: CPU / Memory / Storage -->
      <div
        v-else-if="liveStats"
        class="flex sm:flex-row flex-col bg-surface-white mb-4 border rounded-6 border-outline-gray-2 divide-outline-gray-2 sm:divide-x overflow-hidden"
      >
        <div
          v-for="meter in liveMeters"
          :key="meter.label"
          class="flex-1 px-4 sm:px-5 py-3 sm:py-4 border-t first:border-t-0 sm:border-t-0 border-outline-gray-2"
        >
          <div class="flex justify-between items-baseline gap-2 mb-2">
            <span class="text-ink-gray-6 text-sm">{{ meter.label }}</span>
            <span class="text-ink-gray-6 text-sm shrink-0">{{ meter.value }}</span>
          </div>

          <div class="bg-surface-gray-2 rounded-full h-1 overflow-hidden">
            <div
              class="bg-surface-gray-9 rounded-full h-full"
              :style="{ width: Math.min(meter.percent, 100) + '%' }"
            />
          </div>
        </div>
      </div>

      <!-- Historical empty states -->
      <template v-if="isHistorical && !historyLoading">
        <ErrorMessage v-if="historyError" :message="historyError" />
        <EmptyState
          v-else-if="allEmpty"
          icon="lucide-chart-line"
          :title="`No data for the last ${windowLabel}`"
          description="Monitoring hasn't collected metrics in this range yet."
        />
      </template>

      <div class="gap-4 grid md:grid-cols-2">
        <template v-if="showCharts">
          <ChartCard v-for="chart in charts" :key="chart.title" :title="chart.title">
            <AreaChart v-bind="chart.config" class="min-h-[300px]" />
          </ChartCard>
        </template>

        <template v-else-if="!isHistorical || historyLoading">
          <Skeleton v-for="i in 6" :key="i" class="rounded-6 h-[340px]" />
        </template>
      </div>

      <!-- WAF analytics (only renders when the WAF has logged activity) -->
      <WafAnalytics :window="activeWindow" />
    </template>
  </div>
</template>
