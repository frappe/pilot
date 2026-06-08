<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Badge, LoadingText, ErrorMessage, Progress, AxisChart } from 'frappe-ui'
const router = useRouter()

const data = ref(null)
const loading = ref(true)
const error = ref('')

async function refresh() {
  try {
    const res = await fetch('/api/dashboard')
    if (!res.ok) throw new Error(`${res.status}`)
    data.value = await res.json()
  } catch (e) {
    if (!data.value) error.value = e.message
  } finally {
    loading.value = false
  }
}

const MAX_HISTORY = 60
const stats = ref(null)
const history = ref([])

async function loadStats() {
  try {
    const res = await fetch('/api/stats')
    if (!res.ok) return
    const s = await res.json()
    stats.value = s
    history.value = [
      ...history.value.slice(-(MAX_HISTORY - 1)),
      { time: new Date(), CPU: s.cpu_percent, Memory: s.memory_percent },
    ]
  } catch {}
}

function formatBytes(bytes) {
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(0) + ' KB'
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return (bytes / 1024 ** 3).toFixed(1) + ' GB'
}

function datasetLabel(name) {
  return name.split('/').pop()
}

function datasetPercent(dataset) {
  return dataset.quota_bytes ? (dataset.used_bytes / dataset.quota_bytes) * 100 : 0
}

const POOL_HEALTH_THEME = { ONLINE: 'green', DEGRADED: 'yellow' }

function poolHealthTheme(health) {
  return POOL_HEALTH_THEME[health] ?? 'red'
}

function diskPercent(bytes) {
  return stats.value ? (bytes / stats.value.disk_total) * 100 : 0
}

const chartConfig = computed(() => ({
  title: 'CPU & Memory',
  data: history.value,
  xAxis: { key: 'time', type: 'time', timeGrain: 'second' },
  yAxis: { yMin: 0, yMax: 100, echartOptions: { name: '' } },
  series: [
    { name: 'CPU', type: 'area' },
    { name: 'Memory', type: 'area' },
  ],
}))

let dashTimer, statsTimer

onMounted(() => {
  refresh()
  loadStats()
  dashTimer = setInterval(refresh, 10000)
  statsTimer = setInterval(loadStats, 3000)
})
onUnmounted(() => {
  clearInterval(dashTimer)
  clearInterval(statsTimer)
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <template v-else-if="data">
      <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <button
          v-for="{ count, total, label, route } in [
            { count: data.cloned_count, total: data.apps.length, label: 'Apps cloned', route: '/apps' },
            { count: data.online_count, total: data.sites.length, label: 'Sites online', route: '/sites' },
            { count: data.running_count, total: data.processes.length, label: 'Processes running', route: '/processes' },
            { count: data.recent_tasks.length, total: null, label: 'Recent tasks', route: '/tasks' },
          ]"
          :key="label"
          class="rounded-lg border border-outline-gray-1 bg-surface-white px-6 py-5 text-left shadow-sm transition-colors hover:bg-surface-gray-1"
          @click="router.push(route)"
        >
          <h2 class="text-xl font-semibold text-ink-gray-9">
            {{ total !== null ? `${count} / ${total}` : String(count) }}
          </h2>
          <p class="mt-1.5 text-base text-ink-gray-5">{{ label }}</p>
        </button>
      </div>

      <div v-if="stats" class="rounded-lg border border-outline-gray-1 bg-surface-white px-6 py-5 shadow-sm">
        <div class="mb-4 flex items-center justify-between">
          <h2 class="text-xl font-semibold text-ink-gray-9">Server Stats</h2>
          <span class="flex items-center gap-1.5 text-xs text-ink-gray-4">
            <span class="h-2 w-2 animate-pulse rounded-full bg-green-500" />
            Live
          </span>
        </div>

        <div class="flex flex-col gap-6">
          <div class="grid grid-cols-3 gap-6">
            <div>
              <div class="mb-2 flex items-baseline justify-between">
                <span class="text-sm font-medium text-ink-gray-7">CPU</span>
                <span class="text-sm font-semibold text-ink-gray-9">{{ stats.cpu_percent.toFixed(1) }}%</span>
              </div>
              <Progress :value="stats.cpu_percent" size="md" />
            </div>

            <div>
              <div class="mb-2 flex items-baseline justify-between">
                <span class="text-sm font-medium text-ink-gray-7">Memory</span>
                <span class="text-sm text-ink-gray-5">{{ formatBytes(stats.memory_used) }} / {{ formatBytes(stats.memory_total) }}</span>
              </div>
              <Progress :value="stats.memory_percent" size="md" />
            </div>

            <div v-if="stats.volume?.enabled">
              <div class="mb-2 flex items-baseline justify-between">
                <span class="text-sm font-medium text-ink-gray-7">ZFS Pool</span>
                <Badge :label="stats.volume.pool_health" :theme="poolHealthTheme(stats.volume.pool_health)" />
              </div>
              <p class="font-mono text-xs text-ink-gray-4">{{ stats.volume.pool }}</p>
            </div>
            <div v-else>
              <div class="mb-2 flex items-baseline justify-between">
                <span class="text-sm font-medium text-ink-gray-7">Disk</span>
                <span class="text-sm text-ink-gray-5">{{ formatBytes(stats.disk_used) }} / {{ formatBytes(stats.disk_total) }}</span>
              </div>
              <Progress :value="stats.disk_percent" size="md" />
            </div>
          </div>

          <div v-if="stats.volume?.enabled" class="grid grid-cols-3 gap-6">
            <div v-for="dataset in stats.volume.datasets" :key="dataset.name">
              <div class="mb-2 flex items-baseline justify-between">
                <span class="text-sm font-medium capitalize text-ink-gray-7">{{ datasetLabel(dataset.name) }}</span>
                <span class="text-sm text-ink-gray-5">{{ formatBytes(dataset.used_bytes) }} / {{ formatBytes(dataset.quota_bytes) }}</span>
              </div>
              <Progress :value="datasetPercent(dataset)" size="md" />
              <p class="mt-1 text-xs text-ink-gray-4">
                {{ formatBytes(dataset.available_bytes) }} available · {{ formatBytes(dataset.reservation_bytes) }} reserved
              </p>
            </div>
            <div>
              <div class="mb-2 flex items-baseline justify-between">
                <span class="text-sm font-medium text-ink-gray-7">Root Disk</span>
                <span class="text-sm text-ink-gray-5">{{ formatBytes(stats.disk_used) }} / {{ formatBytes(stats.disk_total) }}</span>
              </div>
              <Progress :value="stats.disk_percent" size="md" />
            </div>
          </div>

          <div v-else-if="stats.paths?.length" class="grid grid-cols-3 gap-6">
            <div v-for="pathInfo in stats.paths" :key="pathInfo.path">
              <div class="mb-2 flex items-baseline justify-between">
                <span class="text-sm font-medium text-ink-gray-7">{{ pathInfo.label }}</span>
                <span class="text-sm text-ink-gray-5">{{ formatBytes(pathInfo.used_bytes) }}</span>
              </div>
              <Progress :value="diskPercent(pathInfo.used_bytes)" size="md" />
            </div>
          </div>

          <AxisChart v-if="history.length > 1" :config="chartConfig" />
        </div>
      </div>
    </template>
  </div>
</template>
