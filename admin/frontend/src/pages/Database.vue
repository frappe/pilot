<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Tabs, ListView, LoadingText, ErrorMessage } from 'frappe-ui'

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
  binlogs.value.map(l => ({ ...l, _size: fmtSize(l.file_size) }))
)

async function loadBinlogs() {
  binlogsLoading.value = true
  binlogsError.value = ''
  try {
    const res = await fetch('/api/database/binlogs')
    if (!res.ok) throw new Error(await res.text())
    binlogs.value = await res.json()
  } catch (e) {
    binlogsError.value = e.message
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
  queries.value.map(q => ({
    ...q,
    _time: new Date(q.timestamp).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }),
    _query_time: `${q.query_time.toFixed(3)}s`,
    _rows: `${q.rows_sent} / ${q.rows_examined}`,
  }))
)

async function loadQueries() {
  queriesLoading.value = true
  queriesError.value = ''
  try {
    const res = await fetch('/api/database/slow-queries?limit=50')
    if (!res.ok) throw new Error(await res.text())
    queries.value = await res.json()
  } catch (e) {
    queriesError.value = e.message
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
]

async function loadProcesses() {
  processesError.value = ''
  try {
    const res = await fetch('/api/database/processlist')
    if (!res.ok) throw new Error(await res.text())
    processes.value = await res.json()
  } catch (e) {
    processesError.value = e.message
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
  { label: 'Binary Logs' },
  { label: 'Slow Queries' },
  { label: 'Process List' },
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

      <!-- Binary Logs -->
      <div v-if="tab.label === 'Binary Logs'" class="pt-4">
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
      <div v-else-if="tab.label === 'Slow Queries'" class="pt-4">
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

      <!-- Process List -->
      <div v-else-if="tab.label === 'Process List'" class="pt-4">
        <div class="mb-3 flex items-center justify-between">
          <span class="text-sm text-ink-gray-5">{{ processes.length }} connection{{ processes.length !== 1 ? 's' : '' }}</span>
          <span class="flex items-center gap-1.5 text-xs text-ink-gray-4">
            <span class="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
            Refreshing every 5s
          </span>
        </div>
        <ErrorMessage v-if="processesError" :message="processesError" />
        <LoadingText v-else-if="processesLoading" />
        <ListView
          v-else
          :columns="processColumns"
          :rows="processes"
          row-key="id"
          :options="{ selectable: false, showTooltip: false }"
        >
          <template #cell="{ column, item }">
            <span
              v-if="column.key === 'time'"
              :class="item > 30 ? 'font-semibold text-red-600' : item > 5 ? 'text-amber-600' : ''"
            >{{ item }}</span>
            <span v-else class="truncate max-w-xs block">{{ item }}</span>
          </template>
        </ListView>
      </div>

    </template>
  </Tabs>
</template>
