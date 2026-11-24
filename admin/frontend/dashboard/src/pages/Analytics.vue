<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, type WritableComputedRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ErrorMessage, Select, Skeleton, toast } from 'frappe-ui'
import { AreaChart, useChartTokens } from 'frappe-ui/charts'
import type {
  AreaChartProps,
  ChartXAxisOptions,
  ReferenceLine,
  SeriesStyle,
  TimeGrain,
} from 'frappe-ui/charts'

import ChartCard from '@/components/common/ChartCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import WafAnalytics from '@/components/common/WafAnalytics.vue'
import DatabaseInsights from '@/components/dashboard/DatabaseInsights.vue'
import SiteInsights from '@/components/dashboard/SiteInsights.vue'
import SystemStats from '@/components/dashboard/SystemStats.vue'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { monitorApi } from '@/api/monitor'
import { settingsApi } from '@/api/settings'
import { useSites } from '@/composables/sites/useSites'
import { errorMessage } from '@/utils/error'
import { livePollDelayMs } from '@/utils/livePolling'
import { withDateTime } from '@/utils/time'
import type {
  ApplicationMetricsHistory,
  CpuBreakdown,
  DiskIoRates,
  MemoryBreakdown,
  NetworkRates,
  SystemMetrics,
  SystemMetricsHistory,
} from '@/types/stats'

type MetricPoint = Record<string, number | null | undefined>

type AlertKey = 'cpu_usage_limit' | 'memory_usage_limit' | 'disk_space_limit'

interface AnalyticsChart {
  title: string
  alert?: AlertKey
  config: AreaChartProps
}

const WINDOWS = [
  { key: 'live', label: 'Live' },
  { key: '30m', label: '30 minutes' },
  { key: '1h', label: '1 hour' },
  { key: '6h', label: '6 hours' },
  { key: '12h', label: '12 hours' },
  { key: '24h', label: '24 hours' },
  { key: '1w', label: '1 week' },
]
const WINDOW_SECONDS: Record<string, number> = {
  '30m': 1800,
  '1h': 3600,
  '6h': 21600,
  '12h': 43200,
  '24h': 86400,
  '1w': 604800,
}
const TIME_GRAIN: Record<string, TimeGrain> = {
  live: 'second',
  '30m': 'minute',
  '1h': 'minute',
  '6h': 'hour',
  '12h': 'hour',
  '24h': 'hour',
  '1w': 'day',
}

const PALETTE = [1, 7, 3, 5, 9, 2, 10]
const LIVE_WINDOW_MS = 1800 * 1000

// Series names and colors
const CPU_SERIES = ['Busy System', 'Busy User', 'Busy IOWait', 'Busy IRQ', 'Busy Other']
const MEMORY_SERIES = ['Used', 'Cached + Buffers', 'Free', 'Swap Used']
const NETWORK_SERIES = ['Received', 'Sent']
const DISK_IO_SERIES = ['Read', 'Write']
const DISK_SERIES = 'Root Disk'

const CPU_COLORS: Record<string, number> = {
  'Busy User': 1,
  'Busy System': 7,
  'Busy IOWait': 9,
  'Busy IRQ': 5,
  'Busy Other': 10,
}
const MEMORY_COLORS: Record<string, number> = {
  Used: 7,
  'Cached + Buffers': 1,
  Free: 3,
  'Swap Used': 9,
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

const routeView = route.query.view
const routeWindow = route.query.window

const view = ref(
  typeof routeView === 'string' && VIEW_KEYS.includes(routeView) ? routeView : 'system',
)

const initialWindow =
  typeof routeWindow === 'string' && WINDOW_KEYS.includes(routeWindow) ? routeWindow : 'live'
const activeWindow = ref(view.value !== 'system' && initialWindow === 'live' ? '1h' : initialWindow)
const isHistorical = computed(() => activeWindow.value !== 'live')

const isServerScope = computed(() => view.value !== 'site')

// Target, not metric: returning to the server keeps the metric on show. A
// site has none of its own, so it has to land on System.
const targetOptions = computed(() => [
  { label: 'Server', value: 'server', icon: 'lucide-server' },
  ...sites.value.map((site) => ({ label: site.name, value: site.name, icon: 'lucide-pilot-site' })),
])
const target = computed({
  get: () => (view.value === 'site' ? activeSite.value : 'server'),
  set: (value) =>
    value === 'server' ? setView(isServerScope.value ? view.value : 'system') : selectSite(value),
})

const metricIcons: Record<string, string> = { system: 'lucide-gauge', database: 'lucide-database' }

// Server-level metrics only; a site has one chart set and needs no picker.
const metricOptions = VIEWS.filter((v) => v.key !== 'site').map((v) => ({
  label: v.label,
  value: v.key,
  icon: metricIcons[v.key],
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

const chooseWindow = (key: string) => {
  preferredWindow = key
  selectWindow(key)
}

const setView = (key: string) => {
  // Re-picking would refetch and, in live mode, discard collected history.
  if (view.value === key) return
  view.value = key
  if (key === 'system') setWindow(preferredWindow)
  else if (activeWindow.value === 'live') selectWindow('1h')
}

const selectSite = (name: string) => {
  setView('site')
  activeSite.value = name
}

const selectWindow = (key: string) => {
  if (view.value === 'system') setWindow(key)
  else activeWindow.value = key
}

// Site view
const { sites, loading: sitesLoading, load: loadSites } = useSites()
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

const limits = ref<Record<AlertKey, number>>({
  cpu_usage_limit: 0,
  memory_usage_limit: 0,
  disk_space_limit: 0,
})
const cpuCount = ref<number | null>(null)

// Live mode state
const stats = ref<SystemMetrics | null>(null)
const statsError = ref('')
const liveHistory = ref<MetricPoint[]>([])
const liveNow = ref(Date.now())
const timeOffset = ref(0)

// Historical mode state
const system = ref<SystemMetricsHistory>({
  earliest: null,
  points: [],
  memory_total_bytes: null,
  storage: null,
})

const application = ref<ApplicationMetricsHistory>({
  earliest: null,
  services: [],
  cpu: [],
  memory: [],
})
// A remote MariaDB server's VM; null for a local server.
const databaseServer = ref<SystemMetricsHistory | null>(null)
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

// Data loading

const setWindow = async (key: string) => {
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
    statsError.value = ''
    liveNow.value = serverTime()
    appendLivePoint(s)
    trimLiveHistory()
  } catch (caught) {
    statsError.value = errorMessage(caught, 'Failed to load live metrics.')
  }
  await refreshAppData()
}

const appendLivePoint = (s: SystemMetrics) => {
  const cpu: Partial<CpuBreakdown> = s.cpu_breakdown || {}
  const mem: Partial<MemoryBreakdown> = s.memory_breakdown || {}
  const [load1, load5, load15] = s.load_avg || []
  const network: Partial<NetworkRates> = s.network || {}
  const diskIo: Partial<DiskIoRates> = s.disk_io || {}
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
  liveHistory.value = liveHistory.value.filter((p) => (p.time ?? 0) >= cutoff)
}

const refreshAppData = async () => {
  try {
    const d = await monitorApi.history('30m')
    if (!hasApiError(d) && d.application) {
      application.value = d.application
      databaseServer.value = d.database_server
    }
  } catch {}
}

const seedLiveHistory = async () => {
  if (isHistorical.value || liveHistory.value.length) return
  try {
    const d = await monitorApi.history('1h')
    if (hasApiError(d)) throw new Error(apiErrorMessage(d, 'Failed to load analytics.'))

    const serverNow = d.now ?? Date.now()
    timeOffset.value = serverNow - Date.now()
    if (d.system?.points?.length) {
      liveHistory.value = d.system.points
      liveNow.value = serverNow
    }
    if (d.application) {
      application.value = d.application
    }
    databaseServer.value = d.database_server
  } catch {}
}

const loadHistory = async (window: string) => {
  historyLoading.value = true
  historyError.value = ''
  try {
    const d = await monitorApi.history(window)
    if (hasApiError(d)) throw new Error(apiErrorMessage(d, 'Failed to load analytics.'))

    historyNow.value = d.now ?? Date.now()
    system.value = d.system
    application.value = d.application
    databaseServer.value = d.database_server
  } catch (caught) {
    historyError.value = errorMessage(caught, 'Failed to load analytics.')
  } finally {
    historyLoading.value = false
  }
}

// Derived state

const liveStats = computed(() => (!isHistorical.value ? stats.value : null))

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
const fixedXAxis = computed<ChartXAxisOptions>(() => ({
  type: 'time',
  timeGrain: TIME_GRAIN[activeWindow.value],
  echartOptions: { min: axisMin.value, max: axisMax.value, splitLine: GRID },
}))

const liveXAxis = computed<ChartXAxisOptions>(() => ({
  type: 'time',
  timeGrain: 'second',
  echartOptions: { min: liveNow.value - LIVE_WINDOW_MS, max: liveNow.value, splitLine: GRID },
}))

const currentPoints = computed<MetricPoint[]>(() =>
  isHistorical.value ? system.value.points : liveHistory.value,
)
const currentXAxis = computed(() => (isHistorical.value ? fixedXAxis.value : liveXAxis.value))

const { tokens } = useChartTokens(ref())

const lineSeries = (slot: number): SeriesStyle => ({
  color: tokens.value.categorical[slot - 1],
  showDataPoints: false,
})

const alertLine = (limit: number, value = limit): ReferenceLine[] =>
  limit
    ? [{ value, label: `Alert at ${limit}%`, dashed: true, color: tokens.value.categorical[6] }]
    : []

const styleFor = (names: string[], colorAt: (name: string, index: number) => number) =>
  Object.fromEntries(
    names.map((name, i): [string, SeriesStyle] => [name, lineSeries(colorAt(name, i))]),
  )

const scaleFields = (points: MetricPoint[], keys: string[], divisor: number): MetricPoint[] => {
  return points.map((p) => ({
    ...p,

    ...Object.fromEntries(
      keys.map((k) => {
        const value = p[k]
        return [k, value != null ? value / divisor : value]
      }),
    ),
  }))
}

const normalizeAppData = (
  points: Record<string, number | null>[],
  services: string[],
): MetricPoint[] => {
  return points.map((p) => ({
    time: p.time,
    ...Object.fromEntries(services.map((s) => [s, p[s] ?? 0])),
  }))
}

// Chart configs

const cpuChart = (
  points: MetricPoint[],
  xAxis: ChartXAxisOptions,
  hasAlerts: boolean,
): AnalyticsChart => ({
  title: 'CPU',
  alert: hasAlerts ? 'cpu_usage_limit' : undefined,
  config: {
    data: points.map((p) => ({
      time: p.time,
      'Busy User': p['Busy User'] ?? 0,
      'Busy System': p['Busy System'] ?? 0,
      'Busy IOWait': p['Busy IOWait'] ?? 0,
      'Busy IRQ': p['Busy IRQ'] ?? 0,
      'Busy Other': p['Busy Other'] ?? 0,
    })),
    xAxis,
    yAxis: { min: 0, max: 100, echartOptions: { name: '%', splitLine: GRID } },
    x: 'time',
    y: CPU_SERIES,
    referenceLines: hasAlerts ? alertLine(limits.value.cpu_usage_limit) : [],
    seriesConfig: styleFor(CPU_SERIES, (name) => CPU_COLORS[name]),
  },
})

const loadChart = (points: MetricPoint[], xAxis: ChartXAxisOptions): AnalyticsChart => ({
  title: 'Load Average',
  config: {
    data: points.map((p) => ({
      time: p.time,
      'Load Average 1': p.Load1 ?? 0,
      'Load Average 5': p.Load5 ?? 0,
      'Load Average 15': p.Load15 ?? 0,
    })),
    xAxis,
    yAxis: { min: 0, echartOptions: { name: '', splitLine: GRID } },
    x: 'time',
    y: ['Load Average 1', 'Load Average 5', 'Load Average 15'],
    seriesConfig: {
      'Load Average 1': lineSeries(3),
      'Load Average 5': lineSeries(7),
      'Load Average 15': lineSeries(9),
    },
  },
})

const memoryTotal = computed(() =>
  isHistorical.value ? system.value.memory_total_bytes : (stats.value?.memory_total ?? null),
)

const memoryChart = (
  points: MetricPoint[],
  xAxis: ChartXAxisOptions,
  hasAlerts: boolean,
): AnalyticsChart => {
  const data = scaleFields(points, MEMORY_SERIES, 1024 ** 3)
  const peak = data.reduce(
    (max, p) =>
      Math.max(
        max,
        MEMORY_SERIES.reduce((sum, k) => sum + (p[k] || 0), 0),
      ),
    0,
  )
  // Only the app server's total is known; another server's axis follows its peak.
  const total = hasAlerts ? (memoryTotal.value ?? 0) / 1024 ** 3 : 0
  const limit = limits.value.memory_usage_limit

  return {
    title: 'Memory',
    alert: hasAlerts ? 'memory_usage_limit' : undefined,
    config: {
      data,
      xAxis,
      yAxis: {
        min: 0,
        max: total || (peak > 0 ? peak * 1.1 : undefined),
        echartOptions: { name: 'GB', splitLine: GRID },
      },
      x: 'time',
      y: MEMORY_SERIES,
      referenceLines: total ? alertLine(limit, (total * limit) / 100) : [],
      seriesConfig: styleFor(MEMORY_SERIES, (name) => MEMORY_COLORS[name]),
    },
  }
}

const diskInfo = computed(() =>
  isHistorical.value
    ? system.value.storage?.disk
    : stats.value
      ? { used_bytes: stats.value.disk_used, total_bytes: stats.value.disk_total }
      : null,
)

const diskChart = (
  points: MetricPoint[],
  xAxis: ChartXAxisOptions,
  hasAlerts: boolean,
): AnalyticsChart => ({
  title: 'Disk',
  alert: hasAlerts ? 'disk_space_limit' : undefined,
  config: {
    data: points,
    xAxis,
    yAxis: { min: 0, max: 100, echartOptions: { name: '%', splitLine: GRID } },
    x: 'time',
    y: DISK_SERIES,
    referenceLines: hasAlerts ? alertLine(limits.value.disk_space_limit) : [],
    seriesConfig: { [DISK_SERIES]: lineSeries(PALETTE[0]) },
  },
})

const networkChart = (points: MetricPoint[], xAxis: ChartXAxisOptions): AnalyticsChart => ({
  title: 'Network',
  config: {
    data: scaleFields(points, NETWORK_SERIES, 1024 ** 2),
    xAxis,
    yAxis: { min: 0, echartOptions: { name: 'MB/s', splitLine: GRID } },
    x: 'time',
    y: NETWORK_SERIES,
    seriesConfig: styleFor(NETWORK_SERIES, (_, i) => PALETTE[i]),
  },
})

const diskIoChart = (points: MetricPoint[], xAxis: ChartXAxisOptions): AnalyticsChart => ({
  title: 'Disk I/O',
  config: {
    data: scaleFields(points, DISK_IO_SERIES, 1024 ** 2),
    xAxis,
    yAxis: { min: 0, echartOptions: { name: 'MB/s', splitLine: GRID } },
    x: 'time',
    y: DISK_IO_SERIES,
    seriesConfig: styleFor(DISK_IO_SERIES, (_, i) => PALETTE[i]),
  },
})

// CPU, load, memory, disk, network and disk I/O of one host, in the order the page shows them.
// Alerts are the app server's: only its charts carry the limits and their lines.
const hostCharts = (
  points: MetricPoint[],
  xAxis: ChartXAxisOptions,
  hasDisk: boolean,
  hasAlerts = false,
) => [
  cpuChart(points, xAxis, hasAlerts),
  loadChart(points, xAxis),
  memoryChart(points, xAxis, hasAlerts),
  ...(hasDisk ? [diskChart(points, xAxis, hasAlerts)] : []),
  networkChart(points, xAxis),
  diskIoChart(points, xAxis),
]

const withChartTimes = (charts: AnalyticsChart[]) =>
  charts.map((chart) => ({
    ...chart,
    config: { ...chart.config, data: withDateTime(chart.config.data ?? []) },
  }))

const appWindowData = computed(() => {
  const data = application.value
  if (!data.services.length) return { services: [], cpu: [], memory: [] }
  if (isHistorical.value) return data
  const latest = data.cpu.length ? Math.max(...data.cpu.map((p) => p.time ?? 0)) : 0
  const cutoff = latest ? latest - LIVE_WINDOW_MS : 0
  return {
    services: data.services,
    cpu: cutoff ? data.cpu.filter((p) => (p.time ?? 0) >= cutoff) : data.cpu,
    memory: cutoff ? data.memory.filter((p) => (p.time ?? 0) >= cutoff) : data.memory,
  }
})

const appCpuConfig = computed<AnalyticsChart>(() => ({
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

const appMemConfig = computed<AnalyticsChart>(() => ({
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
const charts = computed(() =>
  withChartTimes([
    ...hostCharts(currentPoints.value, currentXAxis.value, Boolean(diskInfo.value), true),
    ...(appWindowData.value.cpu.length ? [appCpuConfig.value, appMemConfig.value] : []),
  ]),
)

// Live mode keeps the last 30 minutes, as the app server's charts do.
const databaseServerPoints = computed<MetricPoint[]>(() => {
  const points = databaseServer.value?.points ?? []
  if (isHistorical.value) return points
  const cutoff = liveNow.value - LIVE_WINDOW_MS
  return points.filter((p) => (p.time ?? 0) >= cutoff)
})
const databaseServerCharts = computed(() =>
  withChartTimes(
    hostCharts(
      databaseServerPoints.value,
      currentXAxis.value,
      Boolean(databaseServer.value?.storage?.disk),
    ),
  ),
)

// Lifecycle

let statsTimer: ReturnType<typeof setTimeout> | undefined
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

const saveLimit = async (key: AlertKey, value: number) => {
  const previous = limits.value[key]

  limits.value = { ...limits.value, [key]: value }

  try {
    await settingsApi.update({ resource_limits: { [key]: value } })
  } catch (caught) {
    limits.value = { ...limits.value, [key]: previous }

    toast.error(errorMessage(caught, 'Could not change the alert.'))
  }
}

const alertOptions = (key: AlertKey) =>
  [...new Set([70, 80, 90, 95, limits.value[key]])]
    .filter(Boolean)
    .sort((left, right) => left - right)
    .map((value) => ({ label: `Alert at ${value}%`, value: String(value) }))
    .concat({ label: 'Alert off', value: '0' })

const alertModels = Object.fromEntries(
  (['cpu_usage_limit', 'memory_usage_limit', 'disk_space_limit'] as const).map((key) => [
    key,
    computed({
      get: () => String(limits.value[key]),
      set: (value: string) => saveLimit(key, Number(value)),
    }),
  ]),
) as Record<AlertKey, WritableComputedRef<string>>

onMounted(async () => {
  settingsApi
    .get()
    .then((settings) => (limits.value = { ...limits.value, ...settings.resource_limits }))
    .catch(() => null)
  monitorApi
    .systemInfo()
    .then((info) => (cpuCount.value = info.cpu_count))
    .catch(() => null)

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
  <Teleport defer to="#header-actions">
    <Select
      v-model="target"
      :options="targetOptions"
      size="sm"
      side="bottom"
      align="start"
      placeholder="Select site"
      class="min-w-0 max-w-[250px]"
    />

    <Select
      v-if="isServerScope"
      v-model="metric"
      :options="metricOptions"
      size="sm"
      side="bottom"
      align="start"
    />

    <Select v-model="windowModel" :options="windowOptions" size="sm" side="bottom" align="start">
      <template #prefix>
        <span v-if="!isHistorical" class="bg-surface-green-8 rounded-full size-1.5 animate-pulse" />
        <span v-else class="size-4 text-ink-gray-5 lucide-clock" />
      </template>
    </Select>
  </Teleport>

  <div class="p-4">
    <DatabaseInsights v-if="view === 'database'" :window="historyWindow" />

    <template v-else-if="view === 'site'">
      <SiteInsights v-if="activeSite" :site-name="activeSite" :window="historyWindow" />
      <EmptyState
        v-else-if="!sitesLoading"
        icon="lucide-pilot-site"
        title="No sites on this bench yet"
        description="Create a site to start collecting metrics for it."
      />
    </template>

    <template v-else>
      <ErrorMessage
        v-if="!isHistorical && !liveStats && statsError"
        :message="statsError"
        class="mb-4"
      />

      <div
        v-else-if="!showCharts && (!isHistorical || historyLoading)"
        class="gap-4 grid lg:grid-cols-3 mb-4"
      >
        <div
          v-for="i in 3"
          :key="i"
          class="flex flex-col gap-3 bg-surface-gray-0 p-4 border border-outline-gray-1.5 rounded-5"
        >
          <Skeleton class="rounded-4 w-24 h-4" />
          <Skeleton class="rounded-full h-1.5" />
          <Skeleton class="rounded-4 w-40 h-3.5" />
        </div>
      </div>

      <SystemStats
        v-else-if="showCharts"
        :points="currentPoints"
        :cpu-count="cpuCount"
        :memory-total="memoryTotal"
        :disk="diskInfo ?? null"
        :disk-limit="limits.disk_space_limit"
      />

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

      <h2 v-if="databaseServer" class="mb-3 text-base-semibold text-ink-gray-8">App server</h2>
      <div class="gap-4 grid md:grid-cols-2">
        <template v-if="showCharts">
          <ChartCard v-for="chart in charts" :key="chart.title" :title="chart.title">
            <template v-if="chart.alert" #actions>
              <Select
                v-model="alertModels[chart.alert].value"
                :options="alertOptions(chart.alert)"
                variant="ghost"
                size="xs"
                side="bottom"
                align="end"
                class="-my-1 -mr-1.5"
              >
                <template #prefix>
                  <span class="size-3.5 text-ink-gray-5 lucide-bell" />
                </template>
              </Select>
            </template>

            <AreaChart v-bind="chart.config" class="min-h-[300px]" />
          </ChartCard>
        </template>

        <template v-else-if="!isHistorical || historyLoading">
          <Skeleton v-for="i in 6" :key="i" class="rounded-6 h-[340px]" />
        </template>
      </div>

      <section v-if="databaseServer && !historyError" class="mt-6">
        <h2 class="mb-3 text-base-semibold text-ink-gray-8">Database server</h2>
        <div v-if="historyLoading" class="gap-4 grid md:grid-cols-2">
          <Skeleton v-for="i in 6" :key="i" class="rounded-6 h-[340px]" />
        </div>
        <EmptyState
          v-else-if="!databaseServerPoints.length"
          icon="lucide-database"
          :title="`No database server metrics for the last ${isHistorical ? windowLabel : '30 minutes'}`"
          description="The monitor reads them from the database agent on the database server. Check that the agent is running and reachable."
        />
        <div v-else class="gap-4 grid md:grid-cols-2">
          <ChartCard v-for="chart in databaseServerCharts" :key="chart.title" :title="chart.title">
            <AreaChart v-bind="chart.config" class="min-h-[300px]" />
          </ChartCard>
        </div>
      </section>

      <!-- WAF analytics (only renders when the WAF has logged activity) -->
      <WafAnalytics :window="activeWindow" />
    </template>
  </div>
</template>
