<template>
  <div class="bg-surface-white border rounded-lg border-outline-gray-2 overflow-hidden">
    <div class="flex justify-between items-center gap-2 px-4 py-3 border-b border-outline-gray-2">
      <div class="flex items-center gap-1.5">
        <h3 class="font-medium text-ink-gray-8 text-base">Uptime</h3>
        <span class="size-3.5 text-ink-gray-4 lucide-link" />
      </div>
      <div v-if="data && data.production_enabled && data.overall_percent !== null"
        class="flex items-center gap-1 text-sm">
        <span class="font-semibold text-ink-gray-8">{{ formatPercent(data.overall_percent) }}</span>
        <span class="text-ink-gray-5">Overall Uptime</span>
        <span class="size-3.5 text-ink-gray-4 lucide-circle-help"
          title="Percentage of successful pings to /api/method/ping in this window" />
      </div>
    </div>

    <div v-if="loading" class="flex justify-center py-10">
      <LoadingText />
    </div>
    <ErrorMessage v-else-if="error" :message="error" class="m-4" />
    <div v-else-if="data && !data.production_enabled"
      class="flex flex-col items-center gap-1 py-10 text-center">
      <span class="size-6 text-ink-gray-3 lucide-server-off" />
      <p class="font-medium text-ink-gray-7 text-sm">Uptime monitoring is production-only</p>
      <p class="max-w-xs text-ink-gray-5 text-xs">
        This bench isn't in production, so its sites are never pinged. Deploy to production to start
        tracking uptime.
      </p>
    </div>
    <div v-else-if="!data || !data.buckets.length" class="flex flex-col items-center gap-1 py-10 text-center">
      <span class="size-6 text-ink-gray-3 lucide-activity" />
      <p class="text-ink-gray-5 text-xs">No uptime data yet</p>
    </div>
    <div v-else class="relative px-4 pt-4 pb-3">
      <div class="flex items-end gap-[3px] h-10">
        <div v-for="(bucket, i) in data.buckets" :key="bucket.time"
          class="flex-1 rounded-full min-w-[3px] h-full cursor-pointer"
          :class="{ 'bg-surface-gray-3': bucket.percent === null }"
          :style="bucket.percent === null ? {} : { backgroundColor: barColor(bucket.percent) }"
          @mouseenter="hovered = i" @mouseleave="hovered = null" />
      </div>
      <div class="flex justify-between mt-2 text-ink-gray-5 text-xs">
        <span>{{ formatAxisTime(data.buckets[0].time) }}</span>
        <span>{{ formatAxisTime(data.buckets[data.buckets.length - 1].time) }}</span>
      </div>

      <div v-if="hovered !== null"
        class="z-10 absolute bg-surface-gray-8 shadow-lg mb-2 px-3 py-2 rounded-lg text-xs whitespace-nowrap -translate-x-1/2"
        :style="tooltipStyle">
        <div class="font-semibold text-ink-white">
          {{ formatPercent(data.buckets[hovered].percent) }} · {{ formatFullTime(data.buckets[hovered].time) }}
        </div>
        <div class="text-ink-gray-4">{{ formatRange(data.buckets[hovered]) }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ErrorMessage, LoadingText } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'

const props = defineProps({
  siteName: { type: String, required: true },
  window: { type: String, default: '1h' },
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
function barColor(percent) {
  if (percent >= 100) return GREEN
  if (percent > 0) return AMBER
  return RED
}

function formatPercent(percent) {
  return percent === null || percent === undefined ? 'No data' : `${percent.toFixed(2)}%`
}

const FULL_FORMAT = { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }
const TIME_ONLY_FORMAT = { hour: 'numeric', minute: '2-digit' }

function formatFullTime(ms) {
  return new Date(ms).toLocaleString(undefined, FULL_FORMAT)
}

function formatAxisTime(ms) {
  return new Date(ms).toLocaleString(undefined, FULL_FORMAT)
}

function formatRange(bucket) {
  const start = new Date(bucket.time)
  const end = new Date(bucket.time + data.value.bucket_seconds * 1000)
  return `${start.toLocaleTimeString(undefined, TIME_ONLY_FORMAT)} to ${end.toLocaleTimeString(undefined, TIME_ONLY_FORMAT)}`
}

const tooltipStyle = computed(() => {
  if (hovered.value === null || !data.value) return {}
  const count = data.value.buckets.length
  const left = ((hovered.value + 0.5) / count) * 100
  return { left: `${left}%`, bottom: '100%' }
})

async function load() {
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

watch(() => props.window, load)
onMounted(load)
</script>
