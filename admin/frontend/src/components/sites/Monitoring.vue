<template>
  <div class="space-y-4 mt-5">
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
      <ChartCard title="Frequent requests">
        <AxisChart :config="topPathsConfig" class="w-full min-w-0 h-full min-h-[360px] px-2 sm:px-4 py-2" />
      </ChartCard>
      <ChartCard title="Slowest requests">
        <AxisChart :config="slowestRequestsConfig" class="w-full min-w-0 h-full min-h-[360px] px-2 sm:px-4 py-2" />
      </ChartCard>
      <ChartCard title="Frequent background jobs">
        <AxisChart :config="topJobsConfig" class="w-full min-w-0 h-full min-h-[360px] px-2 sm:px-4 py-2" />
      </ChartCard>
      <ChartCard title="Slowest background jobs">
        <AxisChart :config="slowestJobsConfig" class="w-full min-w-0 h-full min-h-[360px] px-2 sm:px-4 py-2" />
      </ChartCard>
      <ChartCard title="Frequent IPs">
        <AxisChart :config="topIpsConfig" class="w-full min-w-0 h-full min-h-[360px] px-2 sm:px-4 py-2" />
      </ChartCard>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { AxisChart, ErrorMessage, LoadingText } from 'frappe-ui'
import ChartCard from '@/components/common/ChartCard.vue'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'

const props = defineProps({ siteName: { type: String, required: true } })

const loading = ref(true)
const error = ref('')
const data = ref(null)

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }
const PALETTE = ['#10b981', '#ef4444', '#2490ef', '#f59e0b', '#8b5cf6']

const empty = computed(() => {
  const d = data.value
  if (!d) return true
  return !d.top_paths.categories.length && !d.slowest_requests.categories.length &&
    !d.top_jobs.categories.length && !d.slowest_jobs.categories.length && !d.top_ips.categories.length
})

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
  return `<div style="max-width:280px;white-space:normal;"><div class="mb-1">${date}</div>${rows}</div>`
}

// series.name must match the data key holding that category's value.
function timelineConfig(timeline, valueLabel) {
  const categories = timeline?.categories ?? []
  return {
    data: timeline?.points ?? [],
    stacked: true,
    xAxis: { key: 'time', type: 'time', timeGrain: 'minute', echartOptions: { splitLine: GRID } },
    yAxis: { yMin: 0, echartOptions: { name: valueLabel, splitLine: GRID } },
    series: categories.map((name, i) => ({ name, type: 'bar', color: PALETTE[i % PALETTE.length] })),
    echartOptions: { tooltip: { formatter: tooltipFormatter } },
  }
}

const topPathsConfig = computed(() => timelineConfig(data.value?.top_paths, 'Requests'))
const slowestRequestsConfig = computed(() => timelineConfig(data.value?.slowest_requests, 'Duration (ms)'))
const topJobsConfig = computed(() => timelineConfig(data.value?.top_jobs, 'Runs'))
const slowestJobsConfig = computed(() => timelineConfig(data.value?.slowest_jobs, 'Duration (ms)'))
const topIpsConfig = computed(() => timelineConfig(data.value?.top_ips, 'Requests'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await sitesApi.monitoring.get(props.siteName)
    if (result.error) throw new Error(apiErrorMessage(result, 'Could not load monitoring data.'))
    data.value = result
  } catch (e) {
    error.value = e.message || 'Could not load monitoring data.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
