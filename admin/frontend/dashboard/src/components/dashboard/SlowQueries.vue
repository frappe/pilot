<script setup lang="ts">
import { computed } from 'vue'
import { BarChart } from 'frappe-ui/charts'

import ChartCard from '@/components/common/ChartCard.vue'

interface Props {
  overview?: Record<string, any> | null
}

const props = withDefaults(defineProps<Props>(), {
  overview: null,
})

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }
const PALETTE = ['#10b981', '#ef4444', '#f59e0b', '#2490ef', '#8b5cf6', '#06b6d4', '#ec4899']

const bucketLabel = (ms, bucketMs) => {
  const date = new Date(ms)
  return bucketMs >= 24 * 3600_000
    ? date.toLocaleDateString([], { month: 'short', day: 'numeric' })
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// Buckets (and their sizing) come pre-computed from the backend, keyed by
// whichever dimension the chart stacks by — site or query text.
const seriesConfig = (rows, keys, yLabel) => {
  const bucketMs = rows.length > 1 ? rows[1].bucket - rows[0].bucket : 300_000
  return {
    data: rows.map((row) => ({ ...row, bucket: bucketLabel(row.bucket, bucketMs) })),
    x: 'bucket',
    y: keys,
    stacked: true,
    xAxis: { type: 'category', echartOptions: { splitLine: GRID } },
    yAxis: { min: 0, title: yLabel, echartOptions: { splitLine: GRID } },
    seriesConfig: Object.fromEntries(
      keys.map((key, i) => [key, { color: PALETTE[i % PALETTE.length] }]),
    ),
  }
}

const charts = computed(() => {
  const sites = props.overview?.sites ?? []
  const queries = props.overview?.queries ?? []
  return [
    {
      title: 'Slow queries by site',
      keys: sites,
      config: seriesConfig(props.overview?.counts ?? [], sites, 'count'),
    },
    {
      title: 'Slowest queries by site',
      keys: sites,
      config: seriesConfig(props.overview?.durations ?? [], sites, 'seconds'),
    },
    {
      title: 'Frequent slow queries',
      keys: queries,
      config: seriesConfig(props.overview?.query_counts ?? [], queries, 'count'),
    },
  ]
})
</script>

<template>
  <ChartCard v-for="chart in charts" :key="chart.title" :title="chart.title">
    <div
      v-if="!chart.keys.length"
      class="flex justify-center items-center min-h-[280px] text-ink-gray-5 text-xs"
    >
      No slow queries recorded yet
    </div>

    <BarChart v-else v-bind="chart.config" class="min-h-[320px]" />
  </ChartCard>
</template>
