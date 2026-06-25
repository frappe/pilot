<script setup>
import { ref, computed, reactive, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Tabs, ListView, LoadingText, ErrorMessage, Button } from 'frappe-ui'

const router = useRouter()

// ── Binlogs ──────────────────────────────────────────────────────────────────
const binlogs = ref([])
const binlogsLoading = ref(true)
const binlogsError = ref('')

const binlogColumns = [
  { label: 'Log Name', key: 'log_name' },
  { label: 'Size', key: '_size', width: '100px' },
]
const binlogRows = computed(() =>
  binlogs.value.map(log => ({ ...log, _size: fmtSize(log.file_size) }))
)

async function fetchBinlogs() {
  const response = await fetch('/api/database/binlogs')
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

async function loadBinlogs() {
  binlogsLoading.value = true
  binlogsError.value = ''
  try {
    binlogs.value = await fetchBinlogs()
  } catch (error) {
    binlogsError.value = error.message
  } finally {
    binlogsLoading.value = false
  }
}

// ── Slow Queries ─────────────────────────────────────────────────────────────
const queries = ref([])
const queriesLoading = ref(true)
const queriesError = ref('')

const queryColumns = [
  { label: 'Time', key: '_time', width: '140px' },
  { label: 'Query Time', key: '_query_time', width: '110px' },
  { label: 'Rows sent/examined', key: '_rows', width: '160px' },
  { label: 'SQL', key: 'sql' },
]
const queryRows = computed(() =>
  queries.value.map(query => ({
    ...query,
    _time: new Date(query.timestamp).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }),
    _query_time: `${query.query_time.toFixed(3)}s`,
    _rows: `${query.rows_sent} / ${query.rows_examined}`,
  }))
)

async function fetchQueries() {
  const response = await fetch('/api/database/slow-queries?limit=50')
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

async function loadQueries() {
  queriesLoading.value = true
  queriesError.value = ''
  try {
    queries.value = await fetchQueries()
  } catch (error) {
    queriesError.value = error.message
  } finally {
    queriesLoading.value = false
  }
}

// ── Process List ──────────────────────────────────────────────────────────────
const processes = ref([])
const processesLoading = ref(true)
const processesError = ref('')

const processColumns = [
  { label: 'ID', key: 'id', width: '70px' },
  { label: 'User', key: 'user', width: '100px' },
  { label: 'DB', key: 'db', width: '140px' },
  { label: 'Command', key: 'command', width: '100px' },
  { label: 'Time (s)', key: 'time', width: '90px' },
  { label: 'State', key: 'state', width: '140px' },
  { label: 'Query', key: 'info' },
  { label: '', key: '_actions', width: '70px' },
]

const processRows = computed(() =>
  processes.value.map(row => ({ ...row, _actions: row.id }))
)

const killing = reactive(new Set())

async function fetchKill(processId) {
  const response = await fetch(`/api/database/processlist/${processId}`, { method: 'DELETE' })
  return response.json()
}

async function killProcess(processId) {
  killing.add(processId)
  processesError.value = ''
  try {
    const result = await fetchKill(processId)
    if (!result.ok) processesError.value = result.error || 'Failed to kill process'
    else await loadProcesses()
  } catch {
    processesError.value = 'Could not reach the server'
  } finally {
    killing.delete(processId)
  }
}

async function fetchProcesses() {
  const response = await fetch('/api/database/processlist')
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

async function loadProcesses() {
  processesError.value = ''
  try {
    processes.value = await fetchProcesses()
  } catch (error) {
    processesError.value = error.message
  } finally {
    processesLoading.value = false
  }
}

// ── Shared ────────────────────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const activeTab = ref(0)
const tabs = [
  { label: 'Process List' },
  { label: 'Slow Queries' },
  { label: 'Binary Logs' },
]

let processTimer

onMounted(() => {
  loadBinlogs()
  loadQueries()
  loadProcesses()
  processTimer = setInterval(loadProcesses, 5000)
})
onUnmounted(() => clearInterval(processTimer))
</script>

<template>
  <Tabs :tabs="tabs" v-model="activeTab">
    <template #tab-panel="{ tab }">

      <!-- Process List -->
      <div v-if="tab.label === 'Process List'" class="px-5 pt-4">
        <div class="mb-3 flex items-center justify-between">
          <span class="text-sm text-ink-gray-5">{{ processes.length }} connection{{ processes.length !== 1 ? 's' : '' }}</span>
          <span class="flex items-center gap-1.5 text-xs text-ink-gray-4">
            <span class="h-2 w-2 rounded-full bg-surface-green-3 animate-pulse" />
            Refreshing every 5s
          </span>
        </div>
        <ErrorMessage v-if="processesError" :message="processesError" />
        <LoadingText v-else-if="processesLoading" />
        <template v-else>
          <!-- Mobile: stacked cards so the Kill action stays reachable without horizontal scroll -->
          <div class="flex flex-col gap-2 md:hidden">
            <p v-if="!processRows.length" class="py-8 text-center text-sm text-ink-gray-4">No active connections.</p>
            <div
              v-for="row in processRows"
              :key="row.id"
              class="rounded-lg border border-outline-gray-1 px-4 py-3"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="truncate font-medium text-ink-gray-8">#{{ row.id }} · {{ row.user }}</span>
                <Button
                  variant="ghost"
                  theme="red"
                  size="sm"
                  :loading="killing.has(row.id)"
                  @click="killProcess(row.id)"
                >Kill</Button>
              </div>
              <div class="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-gray-5">
                <span>{{ row.db || '—' }}</span>
                <span>{{ row.command }}</span>
                <span :class="row.time > 30 ? 'font-semibold text-ink-red-4' : row.time > 5 ? 'text-ink-amber-3' : ''">{{ row.time }}s</span>
                <span v-if="row.state">{{ row.state }}</span>
              </div>
              <p v-if="row.info" class="mt-1.5 line-clamp-3 break-words font-mono text-xs text-ink-gray-6">{{ row.info }}</p>
            </div>
          </div>

          <!-- Desktop: full table -->
          <div class="hidden md:block">
            <ListView
              :columns="processColumns"
              :rows="processRows"
              row-key="id"
              :options="{ selectable: false, showTooltip: false }"
            >
              <template #cell="{ column, item }">
                <Button
                  v-if="column.key === '_actions'"
                  variant="ghost"
                  theme="red"
                  size="sm"
                  :loading="killing.has(item)"
                  @click.stop="killProcess(item)"
                >Kill</Button>
                <span
                  v-else-if="column.key === 'time'"
                  :class="item > 30 ? 'font-semibold text-ink-red-4' : item > 5 ? 'text-ink-amber-3' : ''"
                >{{ item }}</span>
                <span v-else class="truncate max-w-xs block">{{ item }}</span>
              </template>
            </ListView>
          </div>
        </template>
      </div>

      <!-- Binary Logs -->
      <div v-else-if="tab.label === 'Binary Logs'" class="px-5 pt-4">
        <LoadingText v-if="binlogsLoading" />
        <ErrorMessage v-else-if="binlogsError" :message="binlogsError" />
        <ListView
          v-else
          :columns="binlogColumns"
          :rows="binlogRows"
          row-key="log_name"
          :options="{
            getRowRoute: row => `/database/binlogs/${row.log_name}`,
            selectable: false,
            showTooltip: false,
          }"
        />
      </div>

      <!-- Slow Queries -->
      <div v-else-if="tab.label === 'Slow Queries'" class="px-5 pt-4">
        <LoadingText v-if="queriesLoading" />
        <ErrorMessage v-else-if="queriesError" :message="queriesError" />
        <ListView
          v-else
          :columns="queryColumns"
          :rows="queryRows"
          row-key="_time"
          :options="{ selectable: false, showTooltip: false }"
        />
      </div>

    </template>
  </Tabs>
</template>
