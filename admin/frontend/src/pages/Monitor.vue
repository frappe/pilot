<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Badge, ListView, Button, LoadingText, ErrorMessage, Progress, AxisChart } from 'frappe-ui'

const router = useRouter()

// --- System stats ---
const stats = ref(null)
const history = ref([])
const MAX_HISTORY = 60

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

// --- Processes ---
const processes = ref([])
const production = ref(false)
const processLoading = ref(true)
const processError = ref('')
const controlError = ref('')
const controlLoading = ref('')
const paused = ref(false)
const countdownDisplay = ref(15)
let countdown = 15

const STATUS_COLOR = { running: 'green', stopped: 'red', error: 'red', unknown: 'gray' }
const anyRunning = computed(() => processes.value.some(p => p.status === 'running'))

const columns = [
  { label: 'Name', key: 'name', width: '180px' },
  { label: 'Status', key: 'status', width: '100px' },
  { label: 'PID', key: 'pid', width: '70px' },
  { label: 'CPU', key: 'cpu_percent', width: '70px' },
  { label: 'Memory', key: 'pss_mb', width: '90px' },
  { label: 'Uptime', key: 'uptime', width: '100px' },
  { label: 'Log', key: 'log_filename' },
]

async function loadProcesses() {
  try {
    const res = await fetch('/api/processes/')
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    processes.value = d.processes
    production.value = d.production ?? false
  } catch (e) {
    processError.value = e.message
  } finally {
    processLoading.value = false
  }
}

async function control(action) {
  controlLoading.value = action
  controlError.value = ''
  try {
    const res = await fetch(`/api/processes/${action}`, { method: 'POST' })
    const d = await res.json()
    if (!d.ok) { controlError.value = d.error; return }
    await loadProcesses()
  } catch (e) {
    controlError.value = e.message
  } finally {
    controlLoading.value = ''
  }
}

function openLog(filename) {
  router.push({ path: '/logs', query: { file: filename } })
}

let statsTimer, processTimer

onMounted(() => {
  loadStats()
  loadProcesses()
  statsTimer = setInterval(loadStats, 3000)
  processTimer = setInterval(() => {
    if (paused.value) return
    countdown--
    countdownDisplay.value = countdown
    if (countdown <= 0) { countdown = 15; countdownDisplay.value = 15; loadProcesses() }
  }, 1000)
})
onUnmounted(() => {
  clearInterval(statsTimer)
  clearInterval(processTimer)
})
</script>

<template>
  <div class="flex flex-col gap-6">

    <!-- System Stats -->
    <div v-if="stats" class="rounded-lg border border-outline-gray-1 bg-surface-white px-6 py-5 shadow-sm">
      <div class="mb-4 flex items-center justify-between">
        <h2 class="font-semibold text-ink-gray-9">System</h2>
        <span class="flex items-center gap-1.5 text-xs text-ink-gray-4">
          <span class="h-2 w-2 animate-pulse rounded-full bg-surface-green-3" />
          Live
        </span>
      </div>

      <div class="flex flex-col gap-6">
        <div class="grid grid-cols-1 gap-6 sm:grid-cols-3">
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

        <div v-if="stats.volume?.enabled" class="grid grid-cols-1 gap-6 sm:grid-cols-3">
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

        <div v-else-if="stats.paths?.length" class="grid grid-cols-1 gap-6 sm:grid-cols-3">
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

    <!-- Processes -->
    <div class="rounded-lg border border-outline-gray-1 bg-surface-white px-6 py-5 shadow-sm">
      <div class="mb-4 flex items-center justify-between">
        <h2 class="font-semibold text-ink-gray-9">Processes</h2>
        <div class="flex items-center gap-2">
          <span v-if="!paused" class="text-xs text-ink-gray-4">Refreshing in {{ countdownDisplay }}s</span>
          <Button variant="ghost" size="sm" @click="paused = !paused">{{ paused ? 'Resume' : 'Pause' }}</Button>
        </div>
      </div>

      <div v-if="production" class="mb-3 flex items-center gap-2">
        <Button
          variant="subtle"
          :loading="controlLoading === 'start'"
          :disabled="!!controlLoading || anyRunning"
          @click="control('start')"
        >Start</Button>
        <Button
          variant="subtle"
          :loading="controlLoading === 'stop'"
          :disabled="!!controlLoading || !anyRunning"
          @click="control('stop')"
        >Stop</Button>
        <Button
          variant="subtle"
          :loading="controlLoading === 'restart'"
          :disabled="!!controlLoading || !anyRunning"
          @click="control('restart')"
        >Restart</Button>
      </div>

      <ErrorMessage v-if="controlError" :message="controlError" class="mb-3" />

      <LoadingText v-if="processLoading" />
      <ErrorMessage v-else-if="processError" :message="processError" />
      <ListView
        v-else
        :columns="columns"
        :rows="processes"
        row-key="name"
        :options="{ selectable: false, showTooltip: false }"
      >
        <template #cell="{ column, item }">
          <Badge
            v-if="column.key === 'status'"
            :label="item"
            :theme="STATUS_COLOR[item] || 'gray'"
          />
          <span v-else-if="column.key === 'cpu_percent'">
            {{ item != null ? item.toFixed(1) + '%' : '—' }}
          </span>
          <span v-else-if="column.key === 'pss_mb'">
            {{ item != null ? item.toFixed(0) + ' MB' : '—' }}
          </span>
          <button
            v-else-if="column.key === 'log_filename' && item"
            class="text-ink-blue-2 hover:underline"
            @click="openLog(item)"
          >{{ item }}</button>
          <span v-else>{{ item || '—' }}</span>
        </template>
      </ListView>
    </div>

  </div>
</template>
