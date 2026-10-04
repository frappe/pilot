<script setup lang="ts">
import { Select, Tooltip } from 'frappe-ui'
import { AreaChart } from 'frappe-ui/charts'
import { computed, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import type { Store } from '../store'

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store

const WINDOWS = [
  { label: __('30 minutes'), value: '30m' },
  { label: __('1 hour'), value: '1h' },
  { label: __('6 hours'), value: '6h' },
  { label: __('12 hours'), value: '12h' },
  { label: __('24 hours'), value: '24h' },
  { label: __('1 week'), value: '1w' },
]

const TIME_GRAIN = {
  '30m': 'minute',
  '1h': 'minute',
  '6h': 'hour',
  '12h': 'hour',
  '24h': 'hour',
  '1w': 'day',
}

const GRID = { show: true, lineStyle: { type: 'dashed', color: 'var(--outline-gray-2)' } }

const window = ref('24h')
const analytics = ref(null)
const uptime = ref(null)
const error = ref('')

const load = async () => {
  error.value = ''

  try {
    const [monitoring, checks] = await Promise.all([
      store.api.getAnalytics(window.value),
      store.api.getUptime(window.value),
    ])

    analytics.value = monitoring
    uptime.value = checks
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  }
}

watch(
  () => props.active,
  (active) => {
    if (active) load()
  },
  { immediate: true },
)

watch(window, load)

const total = (timeline) =>
  (timeline?.points || []).reduce((sum, point) => sum + (point[timeline.categories[0]] || 0), 0)

const chartConfig = (timeline, palette = 'categorical') => {
  const now = analytics.value?.now ?? Date.now()

  return {
    data: (timeline?.points ?? []).map((point) => ({ ...point, time: new Date(point.time) })),
    x: 'time',
    y: timeline?.categories ?? [],
    xAxis: {
      type: 'time',
      timeGrain: TIME_GRAIN[window.value],
      echartOptions: {
        min: now - (analytics.value?.window_seconds ?? 0) * 1000,
        max: now,
        splitLine: GRID,
      },
    },
    yAxis: { min: 0, echartOptions: { splitLine: GRID } },
    palette,
  }
}

const charts = computed(() => [
  {
    title: __('Requests'),
    config: chartConfig(analytics.value?.requests_over_time),
  },
  {
    title: __('Background jobs'),
    config: chartConfig(analytics.value?.background_jobs_over_time, ['#8b5cf6']),
  },
])

const stats = computed(() => {
  const percent = uptime.value?.overall_percent

  return [
    {
      label: __('Uptime'),
      value: percent == null ? '—' : `${percent.toFixed(2)}%`,
      icon: 'lucide-activity',
    },
    {
      label: __('Requests'),
      value: total(analytics.value?.requests_over_time).toLocaleString(),
      icon: 'lucide-mouse-pointer-click',
    },
    {
      label: __('Background jobs'),
      value: total(analytics.value?.background_jobs_over_time).toLocaleString(),
      icon: 'lucide-cog',
    },
  ]
})

const hasUptime = computed(() => (uptime.value?.buckets || []).some((bucket) => bucket.checks))

const topPages = computed(() => {
  const timeline = analytics.value?.top_paths
  const counts = {}

  for (const point of timeline?.points || []) {
    for (const path of timeline.categories) counts[path] = (counts[path] || 0) + (point[path] || 0)
  }

  const pages = Object.entries(counts)
    .map(([path, count]) => ({ path, count }))
    .filter((page) => page.count)
    .sort((a, b) => b.count - a.count)
    .slice(0, 6)
  const max = Math.max(1, ...pages.map((page) => page.count))

  return pages.map((page) => ({ ...page, width: `${(page.count / max) * 100}%` }))
})

const uptimeClass = (bucket) => {
  if (!bucket.checks) return 'bg-surface-gray-3'
  if (bucket.percent >= 99.9) return 'bg-surface-green-3'
  if (bucket.percent >= 95) return 'bg-surface-amber-3'

  return 'bg-surface-red-5'
}

const formatTime = (time) =>
  new Date(time).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
</script>

<template>
  <Panel
    :title="__('Analytics')"
    :description="__('How your site has been doing.')"
    :loading="!analytics && !error"
    :error="analytics ? '' : error"
    :error-title="__(`Couldn't load analytics`)"
    @retry="load"
  >
    <template #actions>
      <Select v-model="window" class="col-start-2 row-span-2 row-start-1 w-36" :options="WINDOWS" />
    </template>

    <div class="grid gap-3 sm:grid-cols-3">
      <section
        v-for="stat in stats"
        :key="stat.label"
        class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 rounded-6 border border-outline-gray-2 p-4"
      >
        <span class="row-span-2 grid size-9 place-items-center rounded-5 bg-surface-gray-2">
          <span :class="[stat.icon, 'size-4']" aria-hidden="true" />
        </span>

        <p class="text-sm text-ink-gray-5">{{ stat.label }}</p>

        <p class="text-xl-semibold tabular-nums text-ink-gray-9">{{ stat.value }}</p>
      </section>
    </div>

    <section v-if="hasUptime" class="mt-4 rounded-6 border border-outline-gray-2 p-4">
      <h3 class="mb-3 text-base-medium text-ink-gray-8">{{ __("Uptime") }}</h3>

      <div class="flex h-8 gap-1">
        <Tooltip
          v-for="bucket in uptime.buckets"
          :key="bucket.time"
          :hover-delay="0"
          :text="`${formatTime(bucket.time)} · ${bucket.checks ? `${bucket.percent}% up` : __('No checks')}`"
        >
          <span class="flex-1 rounded-1" :class="uptimeClass(bucket)" />
        </Tooltip>
      </div>
    </section>

    <div class="mt-4 grid gap-4 lg:grid-cols-2">
      <section
        v-for="chart in charts"
        :key="chart.title"
        class="rounded-6 border border-outline-gray-2 p-4"
      >
        <h3 class="text-base-medium text-ink-gray-8">{{ chart.title }}</h3>

        <AreaChart v-bind="chart.config" class="mt-2 !h-60" />
      </section>
    </div>

    <section class="mt-4 overflow-hidden rounded-6 border border-outline-gray-2">
      <p class="flex items-center justify-between border-b border-outline-gray-2 p-3 text-sm">
        <span class="text-base-medium text-ink-gray-8">{{ __("Top pages") }}</span>

        <span class="text-ink-gray-5">{{ __("Requests") }}</span>
      </p>

      <p v-if="!topPages.length" class="px-5 py-8 text-center text-p-sm text-ink-gray-5">
        {{ __("No requests in this period.") }}
      </p>

      <div v-else class="space-y-1 p-2">
        <p
          v-for="page in topPages"
          :key="page.path"
          class="relative flex items-center justify-between gap-4 rounded-4 px-3 py-2 text-sm"
        >
          <span
            class="absolute inset-y-0 left-0 rounded-4 bg-surface-gray-1"
            :style="{ width: page.width }"
          />

          <span class="relative truncate text-ink-gray-8" :title="page.path">{{ page.path }}</span>

          <span class="relative tabular-nums text-ink-gray-6"
            >{{ page.count.toLocaleString() }}</span
          >
        </p>
      </div>
    </section>
  </Panel>
</template>
