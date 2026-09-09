<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ErrorMessage, Skeleton } from 'frappe-ui'
import { AreaChart, BarChart } from 'frappe-ui/charts'

import ChartCard from '@/components/common/ChartCard.vue'
import SiteUptime from '@/components/dashboard/SiteUptime.vue'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'

interface Props {
  siteName: string
  window?: string
}

const props = withDefaults(defineProps<Props>(), {
  window: '24h',
})

const TIME_GRAIN = {
  '30m': 'minute',
  '1h': 'minute',
  '6h': 'hour',
  '12h': 'hour',
  '24h': 'hour',
  '1w': 'day',
}

const loading = ref(true)
const error = ref('')
const data = ref(null)

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }
const PALETTE = ['#10b981', '#ef4444', '#2490ef', '#f59e0b', '#8b5cf6']

const axisMax = computed(() => data.value?.now ?? Date.now())
const axisMin = computed(() => axisMax.value - (data.value?.window_seconds ?? 0) * 1000)

const timelineConfig = (timeline, valueLabel) => {
  const categories = timeline?.categories ?? []
  return {
    data: timeline?.points ?? [],
    x: 'time',
    y: categories,
    stacked: true,
    xAxis: {
      type: 'time',
      timeGrain: TIME_GRAIN[props.window] ?? 'hour',
      echartOptions: { min: axisMin.value, max: axisMax.value, splitLine: GRID },
    },
    yAxis: { min: 0, title: valueLabel, echartOptions: { splitLine: GRID } },
    seriesConfig: Object.fromEntries(
      categories.map((name, i) => [name, { color: PALETTE[i % PALETTE.length] }]),
    ),
  }
}

const CHARTS = [
  ['requests_over_time', 'Requests', 'Requests', 'line'],
  ['top_ips', 'Requests by IP', 'Requests'],
  ['background_jobs_over_time', 'Background jobs', 'Runs', 'line'],
  ['top_paths', 'Frequent requests', 'Requests'],
  ['slowest_requests', 'Slowest requests', 'Duration (s)'],
  ['avg_request_duration', 'Individual request time (average)', 'Duration (s)'],
  ['top_jobs', 'Frequent background jobs', 'Runs'],
  ['avg_job_duration', 'Individual background job (average)', 'Duration (s)'],
  ['frequent_slow_queries', 'Frequent slow queries', 'Count'],
  ['slowest_queries', 'Top slow queries', 'Duration (s)'],
  ['slowest_jobs', 'Slowest background jobs', 'Duration (s)'],
  ['slowest_reports', 'Slowest reports', 'Duration (s)'],
]

const charts = computed(() =>
  CHARTS.map(([key, title, valueLabel, chartType]) => ({
    key,
    title,
    line: chartType === 'line',
    config: timelineConfig(data.value?.[key], valueLabel),
  })).filter((chart) => chart.key !== 'slowest_reports' || chart.config.y.length),
)

// Rapid window switches can resolve out of order; only the most recently
// started load is allowed to write to state.
let loadGeneration = 0

const load = async () => {
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  try {
    const result = await sitesApi.monitoring.get(props.siteName, props.window)
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

watch(() => [props.siteName, props.window], load)
onMounted(load)
</script>

<template>
  <div class="gap-4 grid md:grid-cols-2">
    <SiteUptime :site-name="siteName" :window="window" />

    <template v-if="loading">
      <Skeleton v-for="i in 12" :key="i" class="rounded-6 h-[340px]" />
    </template>

    <ErrorMessage v-else-if="error" :message="error" class="sm:col-span-2" />

    <template v-else>
      <ChartCard v-for="chart in charts" :key="chart.key" :title="chart.title">
        <div
          v-if="!chart.config.y.length"
          class="flex flex-col flex-1 justify-center items-center gap-1 min-h-[300px] text-center"
        >
          <span class="size-6 text-ink-gray-3 lucide-chart-bar" />
          <p class="font-medium text-ink-gray-7 text-xs">No usage yet</p>
          <p class="text-ink-gray-5 text-xs">Data will appear here once activity is tracked</p>
        </div>

        <component
          :is="chart.line ? AreaChart : BarChart"
          v-else
          v-bind="chart.config"
          class="min-h-[300px]"
        >
          <template #tooltip="{ label, items }">
            <p class="mb-2 text-ink-gray-5 text-p-sm">{{ label }}</p>

            <div
              v-for="item in items"
              :key="item.name"
              class="flex items-start gap-2 text-p-sm"
            >
              <span class="mt-1.5 rounded-1 size-2 shrink-0" :style="{ background: item.color }" />
              <span class="flex-1 min-w-0 text-ink-gray-6 break-words">{{ item.label }}</span>
              <span class="font-semibold text-ink-gray-8 tabular-nums shrink-0">
                {{ item.formattedValue }}
              </span>
            </div>
          </template>
        </component>
      </ChartCard>
    </template>
  </div>
</template>
