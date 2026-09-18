<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ErrorMessage, Skeleton } from 'frappe-ui'
import { AreaChart } from 'frappe-ui/charts'

import ChartCard from '@/components/common/ChartCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import SlowQueries from '@/components/dashboard/SlowQueries.vue'

import { apiErrorMessage } from '@/api/client'
import { monitorApi } from '@/api/monitor'
import { formatBytes } from '@/utils/format'

interface Props {
  window?: string
}

const props = withDefaults(defineProps<Props>(), {
  window: '1h',
})

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
  type: 'time',
  timeGrain: TIME_GRAIN[props.window] ?? 'minute',
  echartOptions: {
    min: (data.value?.now ?? Date.now()) - (data.value?.window_seconds ?? 3600) * 1000,
    max: data.value?.now ?? Date.now(),
    splitLine: GRID,
  },
}))

const areaSeries = (color) => ({
  color,
  smooth: true,
  lineWidth: 1.5,
  showDataPoints: false,
  fillOpacity: 0.2,
})

const bytesAxis = { min: 0, title: 'bytes', format: formatBytes, echartOptions: { splitLine: GRID } }

const thresholds = (entries) =>
  entries.map(([value, label]) => ({ value, label, color: '#ef4444', lineType: 'dashed' }))

const charts = computed(() => [
  {
    title: 'Queries',
    config: {
      data: points.value,
      x: 'time',
      y: QUERY_SERIES,
      xAxis: xAxis.value,
      yAxis: { min: 0, title: 'count', echartOptions: { splitLine: GRID } },
      seriesConfig: Object.fromEntries(QUERY_SERIES.map((n, i) => [n, areaSeries(PALETTE[i])])),
    },
  },
  {
    title: 'DB connections',
    config: {
      data: points.value,
      x: 'time',
      y: ['Connected', 'Max Connections'],
      xAxis: xAxis.value,
      yAxis: { min: 0, title: 'connections', echartOptions: { splitLine: GRID } },
      seriesConfig: {
        Connected: areaSeries(PALETTE[0]),
        'Max Connections': areaSeries(PALETTE[2]),
      },
    },
  },
  {
    title: 'Average row lock time (ms)',
    config: {
      data: points.value,
      x: 'time',
      y: 'Avg Row Lock Wait',
      xAxis: xAxis.value,
      yAxis: { min: 0, title: 'ms', echartOptions: { splitLine: GRID } },
      seriesConfig: { 'Avg Row Lock Wait': areaSeries(PALETTE[3]) },
    },
  },
  {
    title: 'Buffer pool size',
    config: {
      data: points.value,
      x: 'time',
      y: 'Buffer Pool Size',
      xAxis: xAxis.value,
      yAxis: bytesAxis,
      seriesConfig: { 'Buffer Pool Size': areaSeries(PALETTE[5]) },
    },
  },
  {
    title: 'Buffer pool size of total RAM',
    config: {
      data: points.value,
      x: 'time',
      y: 'Buffer Pool % RAM',
      xAxis: xAxis.value,
      yAxis: { min: 0, max: 100, title: '%', echartOptions: { splitLine: GRID } },
      seriesConfig: { 'Buffer Pool % RAM': areaSeries(PALETTE[0]) },
      referenceLines: thresholds([
        [65, 'Too High InnoDB Buffer Pool (65%)'],
        [15, 'Too Low InnoDB Buffer Pool (15%)'],
      ]),
    },
  },
  {
    title: 'Buffer pool miss percent',
    config: {
      data: points.value,
      x: 'time',
      y: 'Buffer Pool Miss %',
      xAxis: xAxis.value,
      yAxis: { min: 0, title: '%', echartOptions: { splitLine: GRID } },
      seriesConfig: { 'Buffer Pool Miss %': areaSeries(PALETTE[1]) },
      referenceLines: thresholds([[1, 'Too High Buffer Pool Miss (1%)']]),
    },
  },
])

// Out-of-order window switches: only the latest load writes state.
let loadGeneration = 0

const load = async () => {
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

<template>
  <div v-if="loading" class="gap-4 grid md:grid-cols-2">
    <Skeleton v-for="i in 6" :key="i" class="rounded-6 h-[340px]" />
  </div>

  <ErrorMessage v-else-if="error" :message="error" />
  <EmptyState
    v-else-if="unsupported"
    icon="lucide-database"
    title="DB analyzer supports MariaDB only"
  />
  <EmptyState
    v-else-if="empty"
    icon="lucide-database"
    title="No data for the selected range"
    description="The monitor hasn't sampled the database in this range yet."
  />

  <div v-else class="gap-4 grid md:grid-cols-2">
    <ChartCard v-for="chart in charts" :key="chart.title" :title="chart.title">
      <AreaChart v-bind="chart.config" class="min-h-[300px]" />
    </ChartCard>

    <SlowQueries :overview="data?.slow_queries" />
  </div>
</template>
