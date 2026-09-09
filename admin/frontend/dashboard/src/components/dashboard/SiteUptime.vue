<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ErrorMessage, Skeleton } from 'frappe-ui'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'

interface Props {
  siteName: string
  window?: string
}

const props = withDefaults(defineProps<Props>(), {
  window: '1h',
})

const loading = ref(true)
const error = ref('')
const data = ref(null)
const hovered = ref(null)

const GREEN = '#10b981'
const AMBER = '#f59e0b'
const RED = '#ef4444'

// Null percent (no checks in this bucket) is handled by the caller via the
// bg-surface-gray-3 class instead - this only covers definite percentages.
const barColor = (percent) => {
  if (percent >= 100) return GREEN
  if (percent > 0) return AMBER
  return RED
}

// Percentages carry the bar palette; everything around them stays body text.
const percentTextStyle = (percent) => {
  return percent === null || percent === undefined ? {} : { color: barColor(percent) }
}

const formatPercent = (percent) => {
  return percent === null || percent === undefined ? 'No data' : `${percent.toFixed(2)}%`
}

// "Mon, Jun 27 2026, 08:45 pm" - Intl never emits exactly this, so the date
// (which would carry a comma before the year) and the time are joined by hand.
const DATE_FORMAT = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
})
const TIME_FORMAT = new Intl.DateTimeFormat('en-US', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: true,
})

const formatTimeOnly = (date) => {
  return TIME_FORMAT.format(date).toLowerCase()
}

const formatFullTime = (ms) => {
  const date = new Date(ms)
  return `${DATE_FORMAT.format(date)} ${date.getFullYear()}, ${formatTimeOnly(date)}`
}

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const result = await sitesApi.uptime.get(props.siteName, props.window)
    if (result.error) throw new Error(apiErrorMessage(result, 'Could not load uptime data.'))
    data.value = result
  } catch (e) {
    error.value = e.message || 'Could not load uptime data.'
  } finally {
    loading.value = false
  }
}

watch(() => [props.siteName, props.window], load)
onMounted(load)
</script>

<template>
  <Skeleton v-if="loading" class="rounded-6 h-full min-h-[340px]" />
  <div v-else class="flex flex-col bg-surface-white border rounded-6 border-outline-gray-2 h-full">
    <h3 class="px-4 py-3 border-b border-outline-gray-2 font-medium text-ink-gray-8">Uptime</h3>

    <ErrorMessage v-if="error" :message="error" class="m-4" />
    <div
      v-else-if="data && !data.production_enabled"
      class="flex flex-col flex-1 justify-center items-center gap-1 py-10 text-center"
    >
      <span class="size-6 text-ink-gray-3 lucide-server-off" />
      <p class="font-medium text-ink-gray-7 text-sm">Uptime monitoring is production-only</p>
      <p class="max-w-xs text-ink-gray-5 text-p-xs">
        This bench isn't in production, so its sites are never pinged. Deploy to production to start
        tracking uptime.
      </p>
    </div>

    <div
      v-else-if="!data || data.overall_percent === null"
      class="flex flex-col flex-1 justify-center items-center gap-1 py-10 text-center"
    >
      <span class="size-6 text-ink-gray-3 lucide-activity" />
      <p class="text-ink-gray-5 text-xs">No uptime data yet</p>
    </div>

    <div v-else class="flex flex-col flex-1 justify-center gap-2 px-4 py-6">
      <div class="flex justify-between items-center gap-2 text-ink-gray-5 text-sm">
        <div v-if="hovered !== null" class="min-w-0 truncate">
          <span class="font-bold" :style="percentTextStyle(data.buckets[hovered].percent)">
            {{ formatPercent(data.buckets[hovered].percent) }}
          </span>

          <span class="mx-1 text-base leading-none">·</span>
          <span>{{ formatFullTime(data.buckets[hovered].time) }}</span>
        </div>

        <div class="flex items-center gap-1 ml-auto shrink-0">
          <span>{{ formatPercent(data.overall_percent) }}</span>
          <span class="text-base leading-none">·</span>
          <span>Overall Uptime</span>
          <span
            class="size-3.5 text-ink-gray-4 lucide-circle-help"
            title="Percentage of successful pings to /api/method/ping in this window"
          />
        </div>
      </div>

      <div class="flex items-end gap-[3px] h-16" @mouseleave="hovered = null">
        <div
          v-for="(bucket, i) in data.buckets"
          :key="bucket.time"
          class="flex-1 hover:brightness-[110%] rounded-full min-w-[3px] h-full"
          :class="{ 'bg-surface-gray-3': bucket.percent === null }"
          :style="bucket.percent === null ? {} : { backgroundColor: barColor(bucket.percent) }"
          @mouseenter="hovered = i"
        />
      </div>

      <div class="flex justify-between text-ink-gray-5 text-xs">
        <span>{{ formatFullTime(data.buckets[0].time) }}</span>
        <span>{{ formatFullTime(data.buckets[data.buckets.length - 1].time) }}</span>
      </div>
    </div>
  </div>
</template>
