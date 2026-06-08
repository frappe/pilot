<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Badge, ListView, Button, LoadingText, ErrorMessage } from 'frappe-ui'

const processes = ref([])
const production = ref(false)
const loading = ref(true)
const error = ref('')
const controlError = ref('')
const controlLoading = ref('')   // 'start' | 'stop' | 'restart' | ''
const paused = ref(false)
const countdownDisplay = ref(15)
let countdown = 15
let timer

const router = useRouter()
const STATUS_COLOR = { running: 'blue', stopped: 'red', error: 'red', unknown: 'gray' }

const anyRunning = computed(() => processes.value.some(p => p.status === 'running'))

function openLog(filename) {
  router.push(`/logs/${filename}`)
}

const columns = [
  { label: 'Name', key: 'name', width: '180px' },
  { label: 'Status', key: 'status', width: '100px' },
  { label: 'PID', key: 'pid', width: '70px' },
  { label: 'CPU', key: 'cpu_percent', width: '70px' },
  { label: 'Memory', key: 'memory_mb', width: '90px' },
  { label: 'Uptime', key: 'uptime', width: '100px' },
  { label: 'Log', key: 'log_filename' },
]

const rows = computed(() => processes.value)

async function load() {
  try {
    const res = await fetch('/api/processes/')
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    processes.value = d.processes
    production.value = d.production ?? false
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function control(action) {
  controlLoading.value = action
  controlError.value = ''
  try {
    const res = await fetch(`/api/processes/${action}`, { method: 'POST' })
    const d = await res.json()
    if (!d.ok) { controlError.value = d.error; return }
    await load()
  } catch (e) {
    controlError.value = e.message
  } finally {
    controlLoading.value = ''
  }
}

onMounted(() => {
  load()
  timer = setInterval(() => {
    if (paused.value) return
    countdown--
    countdownDisplay.value = countdown
    if (countdown <= 0) { countdown = 15; countdownDisplay.value = 15; load() }
  }, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="flex flex-col gap-4">
    <div class="flex justify-between items-center gap-2">
      <div v-if="production" class="flex items-center gap-2">
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
      <div v-else />
      <div class="flex items-center gap-2">
        <span v-if="!paused" class="text-sm text-ink-gray-5">Refreshing in {{ countdownDisplay }}s</span>
        <Button variant="ghost" size="sm" @click="paused = !paused">{{ paused ? 'Resume' : 'Pause' }}</Button>
      </div>
    </div>

    <ErrorMessage v-if="controlError" :message="controlError" />

    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <div v-else>
      <ListView
        :columns="columns"
        :rows="rows"
        row-key="name"
        :options="{ selectable: false, showTooltip: false }"
        <template #cell="{ column, item }">
          <Badge
            v-if="column.key === 'status'"
            :label="item"
            :theme="STATUS_COLOR[item] || 'gray'"
          >
            <template v-if="item === 'running'" #prefix>
              <span class="pulse-dot" />
            </template>
          </Badge>
          <span v-else-if="column.key === 'cpu_percent'">
            {{ item != null ? item.toFixed(1) + '%' : '—' }}
          </span>
          <span v-else-if="column.key === 'memory_mb'">
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
