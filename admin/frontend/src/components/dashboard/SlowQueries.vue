<template>
  <ChartCard v-for="chart in charts" :key="chart.title" :title="chart.title">
    <div
      v-if="!chart.keys.length"
      class="flex justify-center items-center min-h-[280px] text-ink-gray-5 text-xs"
    >
      No slow queries recorded yet
    </div>
    <AxisChart
      v-else
      :config="chart.config"
      class="w-full min-w-0 h-full min-h-[320px] px-2 sm:px-4 py-2"
    />
  </ChartCard>
</template>

<script setup>
import { computed } from 'vue'
import { AxisChart } from 'frappe-ui'
import ChartCard from '@/components/common/ChartCard.vue'

const props = defineProps({ overview: { type: Object, default: null } })

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }
const PALETTE = ['#10b981', '#ef4444', '#f59e0b', '#2490ef', '#8b5cf6', '#06b6d4', '#ec4899']

function bucketLabel(ms, bucketMs) {
  const date = new Date(ms)
  return bucketMs >= 24 * 3600_000
    ? date.toLocaleDateString([], { month: 'short', day: 'numeric' })
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// Buckets (and their sizing) come pre-computed from the backend, keyed by
// whichever dimension the chart stacks by — site or query text.
function seriesConfig(rows, keys, yLabel) {
  const bucketMs = rows.length > 1 ? rows[1].bucket - rows[0].bucket : 300_000
  return {
    data: rows.map((row) => ({ ...row, bucket: bucketLabel(row.bucket, bucketMs) })),
    stacked: true,
    xAxis: { key: 'bucket', type: 'category', echartOptions: { splitLine: GRID } },
    yAxis: { yMin: 0, echartOptions: { name: yLabel, splitLine: GRID } },
    series: keys.map((key, i) => ({ name: key, type: 'bar', color: PALETTE[i % PALETTE.length] })),
    // frappe-ui only auto-shows the legend when a chart has more than one series; force it on so a single key still gets its legend.
    echartOptions: { legend: { show: true }, grid: { bottom: 40 } },
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
