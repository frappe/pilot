<template>
  <div class="space-y-4 mt-5">
    <div class="flex justify-end">
      <Dropdown :options="windowOptions" placement="bottom-end">
        <template #default="{ open }">
          <Button variant="outline" size="sm" :active="open">
            <template #suffix><span class="size-4 lucide-chevron-down" /></template>
            {{ windowLabel }}
          </Button>
        </template>
      </Dropdown>
    </div>
    <SiteUptime :site-name="props.siteName" :window="activeWindow" />
    <div v-if="loading" class="flex justify-center py-12">
      <LoadingText />
    </div>
    <ErrorMessage v-else-if="error" :message="error" />
    <div v-else-if="empty" class="flex flex-col justify-center items-center gap-2 h-[40vh] text-center">
      <span class="size-10 text-ink-gray-3 lucide-chart-bar" />
      <p class="font-medium text-ink-gray-7 text-sm">No monitoring data yet</p>
      <p class="max-w-xs text-ink-gray-5 text-xs">
        Requests and background jobs will show up here once Frappe's monitor has logged some activity.
      </p>
    </div>
    <div v-else class="space-y-4">
      <ChartCard v-for="chart in charts" :key="chart.key" :title="chart.title">
        <div v-if="!chart.config.series.length"
          class="flex flex-col justify-center items-center gap-1 min-h-[200px] text-center">
          <span class="size-6 text-ink-gray-3 lucide-chart-bar" />
          <p class="text-ink-gray-5 text-xs">No data in this window</p>
        </div>
        <AxisChart v-else :config="chart.config" class="w-full min-w-0 h-full min-h-[360px] px-2 sm:px-4 py-2" />
      </ChartCard>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { AxisChart, Button, Dropdown, ErrorMessage, LoadingText } from 'frappe-ui'
import ChartCard from '@/components/common/ChartCard.vue'
import SiteUptime from '@/components/sites/Uptime.vue'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'

const props = defineProps({ siteName: { type: String, required: true } })

const WINDOWS = [
  { key: '30m', label: '30 minutes' },
  { key: '1h', label: '1 hour' },
  { key: '6h', label: '6 hours' },
  { key: '12h', label: '12 hours' },
  { key: '24h', label: '24 hours' },
  { key: '1w', label: '1 week' },
]
const TIME_GRAIN = { '30m': 'minute', '1h': 'minute', '6h': 'hour', '12h': 'hour', '24h': 'hour', '1w': 'day' }

const activeWindow = ref('24h')
const windowLabel = computed(() => WINDOWS.find((w) => w.key === activeWindow.value)?.label ?? '')
const windowOptions = computed(() =>
  WINDOWS.map((w) => ({ label: w.label, onClick: () => { activeWindow.value = w.key } })),
)

const loading = ref(true)
const error = ref('')
const data = ref(null)

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }
const PALETTE = ['#10b981', '#ef4444', '#2490ef', '#f59e0b', '#8b5cf6']

const numberFormat = new Intl.NumberFormat()
const dateFormat = { month: 'long', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }

const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
// Series names come from logged request paths/job methods/IPs - attacker-controlled
// data that ends up here as an HTML string, so it must be escaped before interpolation.
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c])

// Custom tooltip: lets the label wrap without breaking the number, and reuses
// ECharts' own marker HTML so the dot color always matches the bar/legend.
function tooltipFormatter(paramsInput) {
  const params = (Array.isArray(paramsInput) ? paramsInput : [paramsInput]).filter((p) => p.value?.[1])
  if (!params.length) return ''
  const rows = params
    .slice()
    .sort((a, b) => b.value[1] - a.value[1])
    .map((p) => `
      <div class="flex items-start gap-2 py-0.5" style="display:flex;white-space:normal;">
        ${p.marker}
        <span class="flex-1 min-w-0" style="flex:1 1 auto;min-width:0;overflow-wrap:break-word;white-space:normal;">${escapeHtml(p.seriesName)}</span>
        <span class="font-bold shrink-0" style="flex:0 0 auto;white-space:nowrap;">${numberFormat.format(p.value[1])}</span>
      </div>
    `)
    .join('')
  const date = new Date(params[0].value[0]).toLocaleString(undefined, dateFormat)
  return `<div style="max-width:420px;white-space:normal;"><div class="mb-1">${date}</div>${rows}</div>`
}

// series.name must match the data key holding that category's value.
function timelineConfig(timeline, valueLabel) {
  const categories = timeline?.categories ?? []
  return {
    data: timeline?.points ?? [],
    stacked: true,
    xAxis: { key: 'time', type: 'time', timeGrain: TIME_GRAIN[activeWindow.value], echartOptions: { splitLine: GRID } },
    yAxis: { yMin: 0, echartOptions: { name: valueLabel, splitLine: GRID } },
    series: categories.map((name, i) => ({ name, type: 'bar', color: PALETTE[i % PALETTE.length] })),
    echartOptions: { tooltip: { formatter: tooltipFormatter } },
  }
}

const charts = computed(() => [
  { key: 'top_paths', title: 'Frequent requests', config: timelineConfig(data.value?.top_paths, 'Requests') },
  { key: 'slowest_requests', title: 'Slowest requests', config: timelineConfig(data.value?.slowest_requests, 'Duration (s)') },
  { key: 'top_jobs', title: 'Frequent background jobs', config: timelineConfig(data.value?.top_jobs, 'Runs') },
  { key: 'slowest_jobs', title: 'Slowest background jobs', config: timelineConfig(data.value?.slowest_jobs, 'Duration (s)') },
  { key: 'top_ips', title: 'Frequent IPs', config: timelineConfig(data.value?.top_ips, 'Requests') },
  { key: 'slowest_reports', title: 'Slowest reports', config: timelineConfig(data.value?.slowest_reports, 'Duration (s)') },
  { key: 'frequent_slow_queries', title: 'Frequent slow queries', config: timelineConfig(data.value?.frequent_slow_queries, 'Count') },
  { key: 'slowest_queries', title: 'Slowest queries', config: timelineConfig(data.value?.slowest_queries, 'Duration (s)') },
])

const empty = computed(() => !data.value || charts.value.every((chart) => !chart.config.series.length))

// Rapid window switches can resolve out of order; only the most recently
// started load is allowed to write to state.
let loadGeneration = 0

async function load() {
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  try {
    const result = await sitesApi.monitoring.get(props.siteName, activeWindow.value)
    if (generation !== loadGeneration) return
    if (result.error) throw new Error(apiErrorMessage(result, 'Could not load monitoring data.'))
    data.value = result
  } catch (e) {
    if (generation !== loadGeneration) return
    error.value = e.message || 'Could not load monitoring data.'
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

watch(activeWindow, load)
onMounted(load)
</script>
