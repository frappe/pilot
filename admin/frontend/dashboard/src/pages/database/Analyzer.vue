<script setup lang="ts">
import { useRoute } from 'vue-router'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import {
  Button,
  Checkbox,
  Dialog,
  ErrorMessage,
  LoadingText,
  Select,
  Skeleton,
  Switch,
  Tooltip,
  toast,
} from 'frappe-ui'

import SizeBreakup from '@/components/database/SizeBreakup.vue'
import DatabasePanel from '@/components/database/DatabasePanel.vue'
import IndexAnalysisPanel from '@/components/database/IndexAnalysisPanel.vue'
import QueryAnalysisPanel from '@/components/database/QueryAnalysisPanel.vue'
import Table from '@/components/common/Table.vue'
import TableSizesDialog from '@/components/database/TableSizesDialog.vue'

import { databaseApi } from '@/api/database'
import { formatBytes } from '@/utils/format'
import { relativeTime } from '@/utils/time'
import { apiErrorMessage } from '@/api/client'


const AUTO_REFRESH_INTERVAL_MS = 2000

const processColumns = [
  { label: '#', key: 'number', class: 'w-10' },
  { label: 'ID', key: 'id', class: 'w-[10%]' },
  { label: 'State', key: 'state', class: 'w-[10%]' },
  { label: 'Time', key: 'time', class: 'w-[10%]' },
  { label: 'User', key: 'user', class: 'w-[10%]' },
  { label: 'Host', key: 'host', class: 'w-[24%]' },
  { label: 'Command', key: 'command', class: 'w-[10%]' },
  { label: 'Query', key: 'query', class: 'w-[12%]' },
  { label: '', key: 'actions', class: 'w-20' },
]

const lockColumns = [
  { label: '#', key: 'number', class: 'w-10' },
  { label: 'ID', key: 'id', class: 'w-[8%]' },
  { label: 'Type', key: 'type', class: 'w-[8%]' },
  { label: 'Mode', key: 'mode', class: 'w-[8%]' },
  { label: 'Table', key: 'table', class: 'w-[12%]' },
  { label: 'Index', key: 'index', class: 'w-[8%]' },
  { label: 'State', key: 'state', class: 'w-[8%]' },
  { label: 'Started', key: 'started', class: 'w-[12%]' },
  { label: 'Query', key: 'query', class: 'w-[15%]' },
  { label: 'Rows Locked', key: 'rowsLocked', class: 'w-[9%]' },
  { label: 'Rows Modified', key: 'rowsModified', class: 'w-[10%]' },
]

const binlogColumns = [
  { label: '#', key: 'number', class: 'w-10' },
  { label: '', key: 'selected', class: 'w-10' },
  { label: 'File', key: 'name', class: 'w-[44%]' },
  { label: 'Date', key: 'date', class: 'w-[33%]' },
  { label: 'Size', key: 'size', class: 'w-[23%]' },
  { label: '', key: 'actions', class: 'w-12' },
]

const route = useRoute()

const loading = ref(false)
const error = ref('')
const diagnostics = ref(null)
const configuredEngine = ref('')
const sites = ref([])
const selectedSite = ref('')

const processes = ref([])
const processesLoading = ref(false)
const processesError = ref('')

const lockWaits = ref([])
const lockWaitsLoading = ref(false)
const lockWaitsError = ref('')
const autoRefreshLocks = ref(true)
let lockWaitsTimer = null
let lockWaitsPollVersion = 0
let lockWaitsRequest = null
let lockWaitsRequestSite = null
let lockWaitsReloadQueued = false

const binlogs = ref([])
const binlogsLoading = ref(false)
const binlogsError = ref('')

const size = ref(null)
const sizeLoading = ref(false)
const sizeError = ref('')
const showTableSizes = ref(false)

const killTarget = ref(null)
const showKillDialog = ref(false)
const killing = ref(false)
const killError = ref('')

const selectedIndex = ref(-1)
const showPurgeDialog = ref(false)
const pendingIndex = ref(-1)
const purging = ref(false)
const purgeError = ref('')

const processRows = computed(() =>
  processes.value.map((process, index) => ({
    number: index + 1,
    id: process.id,
    state: process.state || '—',
    time: formatSeconds(process.duration_seconds),
    user: process.user || '—',
    host: process.host || '—',
    command: process.command || '—',
    query: truncateQuery(process.query),
    process,
  })),
)

const lockRows = computed(() =>
  lockWaits.value.map((row, index) => ({
    number: index + 1,
    id: row.id,
    type: row.type,
    mode: row.mode,
    table: row.table || '—',
    index: row.index || '—',
    state: row.state || '—',
    started: row.started || '—',
    query: truncateQuery(row.query),
    rowsLocked: row.rows_locked ?? '—',
    rowsModified: row.rows_modified ?? '—',
  })),
)

const binlogRows = computed(() =>
  binlogs.value.map((file, index) => ({
    number: index + 1,
    name: file.name,
    date: relativeTime(file.modified_ms),
    size: formatBytes(file.size_bytes),
    index,
    isActive: index === binlogs.value.length - 1,
  })),
)

const killDetails = computed(() => {
  const process = killTarget.value
  if (!process) return []
  return [
    { label: 'User', value: process.user || '—' },
    { label: 'Database', value: process.database || '—' },
    { label: 'State', value: process.command || '—' },
    { label: 'Running for', value: formatSeconds(process.duration_seconds) },
  ]
})

const killQuery = computed(() => killTarget.value?.query || '')

const pendingFiles = computed(() =>
  pendingIndex.value < 0 ? [] : binlogs.value.slice(0, pendingIndex.value + 1),
)
const pendingSize = computed(() =>
  formatBytes(pendingFiles.value.reduce((total, file) => total + file.size_bytes, 0)),
)

// Deletion is always a contiguous run from the oldest file, so the range says
// everything a list of names would - and stays readable at hundreds of files.
const purgeDetails = computed(() => {
  const files = pendingFiles.value
  if (!files.length) return []
  const kept = binlogs.value[pendingIndex.value + 1]
  return [
    { label: 'Oldest deleted', value: files[0].name },
    { label: 'Newest deleted', value: files[files.length - 1].name },
    { label: 'Kept from', value: kept ? kept.name : '—' },
  ]
})

const lockColumnsBadge = computed(() =>
  configuredEngine.value === 'postgres' ? "Some columns aren't available for PostgreSQL" : '',
)

// Binary logs are a MariaDB concept; the engine reports no status when it has none.
const hasBinlogs = computed(() => Boolean(diagnostics.value?.binlog))

// Only sites on this server can be scoped to; a SQLite site owns a file, not a
// database on the bench's engine.
const siteOptions = computed(() => [
  { label: 'All databases', value: '' },
  ...sites.value
    .filter((site) => site.db_type === configuredEngine.value)
    .map((site) => ({ label: site.name, value: site.name })),
])

const scopeBadge = computed(() => selectedSite.value)

const siteByDatabase = computed(() =>
  Object.fromEntries(
    sites.value.filter((site) => site.db_name).map((site) => [site.db_name, site.name]),
  ),
)

const MAX_QUERY_LENGTH = 30

// Long queries can be arbitrarily large single-line strings that would
// otherwise force the table wider than the page.
const truncateQuery = (query) => {
  if (!query) return '—'
  return query.length > MAX_QUERY_LENGTH ? `${query.slice(0, MAX_QUERY_LENGTH)}…` : query
}

const formatSeconds = (seconds) => {
  return seconds == null ? '—' : `${Math.round(seconds)}s`
}

// Purging is contiguous from the oldest file, so ticking one file ticks every
// older file with it and unticking one clears everything newer.
const toggle = (index, checked) => {
  selectedIndex.value = checked ? index : index - 1
}

const confirmKill = (process) => {
  killTarget.value = process
  killError.value = ''
  showKillDialog.value = true
}

const kill = async () => {
  killing.value = true
  killError.value = ''
  try {
    const result = await databaseApi.killProcess(killTarget.value.id)
    if (result.error) throw new Error(apiErrorMessage(result, 'Could not kill the process.'))
    showKillDialog.value = false
    toast.success(`Killed process ${killTarget.value.id}`)
    await loadProcesses()
  } catch (e) {
    killError.value = e.message || 'Could not kill the process.'
  } finally {
    killing.value = false
  }
}

const confirmPurge = (index) => {
  pendingIndex.value = index
  purgeError.value = ''
  showPurgeDialog.value = true
}

const purge = async () => {
  // PURGE keeps the named file, so target the one just after the last selected.
  const keepFrom = binlogs.value[pendingIndex.value + 1]
  if (!keepFrom) return
  purging.value = true
  purgeError.value = ''
  try {
    const result = await databaseApi.binlogs.purge(keepFrom.name)
    if (result.error) throw new Error(apiErrorMessage(result, 'Could not delete binary logs.'))
    showPurgeDialog.value = false
    selectedIndex.value = -1
    toast.success('Binary logs deleted')
    await loadBinlogs()
  } catch (e) {
    purgeError.value = e.message || 'Could not delete binary logs.'
  } finally {
    purging.value = false
  }
}

const loadProcesses = async () => {
  processesLoading.value = true
  processesError.value = ''
  try {
    const result = await databaseApi.processList(selectedSite.value)
    if (result?.error)
      throw new Error(apiErrorMessage(result, 'Could not load database processes.'))
    processes.value = Array.isArray(result) ? result : []
  } catch (e) {
    processesError.value = e.message || 'Could not load database processes.'
  } finally {
    processesLoading.value = false
  }
}

const loadLockWaits = () => {
  if (lockWaitsRequest) {
    if (lockWaitsRequestSite !== selectedSite.value) lockWaitsReloadQueued = true
    return lockWaitsRequest
  }
  lockWaitsRequest = drainLockWaitsRequests()
  return lockWaitsRequest
}

const drainLockWaitsRequests = async () => {
  lockWaitsLoading.value = true
  try {
    do {
      lockWaitsReloadQueued = false
      await fetchLockWaits()
    } while (lockWaitsReloadQueued)
  } finally {
    lockWaitsLoading.value = false
    lockWaitsRequest = null
    lockWaitsRequestSite = null
  }
}

const fetchLockWaits = async () => {
  const site = selectedSite.value
  lockWaitsRequestSite = site
  lockWaitsError.value = ''
  try {
    const result = await databaseApi.lockWaitRows(site)
    if (site !== selectedSite.value) {
      lockWaitsReloadQueued = true
      return
    }
    if (result?.error)
      throw new Error(apiErrorMessage(result, 'Could not load database lock waits.'))
    lockWaits.value = Array.isArray(result) ? result : []
  } catch (e) {
    if (site !== selectedSite.value) lockWaitsReloadQueued = true
    else lockWaitsError.value = e.message || 'Could not load database lock waits.'
  }
}

const loadSize = async () => {
  sizeLoading.value = true
  sizeError.value = ''
  try {
    const result = await databaseApi.size(selectedSite.value)
    if (result?.error) throw new Error(apiErrorMessage(result, 'Could not read the database size.'))
    size.value = result
  } catch (e) {
    size.value = null
    sizeError.value = e.message || 'Could not read the database size.'
  } finally {
    sizeLoading.value = false
  }
}

const openLockWaits = () => {
  loadLockWaits()
  if (autoRefreshLocks.value) startLockWaitsAutoRefresh()
}

const loadBinlogs = async () => {
  binlogsLoading.value = true
  binlogsError.value = ''
  try {
    const result = await databaseApi.binlogs.list()
    if (result?.error) throw new Error(apiErrorMessage(result, 'Could not load binary logs.'))
    binlogs.value = Array.isArray(result) ? result : []
    selectedIndex.value = -1
  } catch (e) {
    binlogsError.value = e.message || 'Could not load binary logs.'
  } finally {
    binlogsLoading.value = false
  }
}

const pollLockWaits = async (version) => {
  await loadLockWaits()
  if (version !== lockWaitsPollVersion || !autoRefreshLocks.value) return
  lockWaitsTimer = setTimeout(() => pollLockWaits(version), AUTO_REFRESH_INTERVAL_MS)
}

const startLockWaitsAutoRefresh = () => {
  stopLockWaitsAutoRefresh()
  const version = lockWaitsPollVersion
  lockWaitsTimer = setTimeout(() => pollLockWaits(version), AUTO_REFRESH_INTERVAL_MS)
}

const stopLockWaitsAutoRefresh = () => {
  lockWaitsPollVersion += 1
  if (lockWaitsTimer) clearTimeout(lockWaitsTimer)
  lockWaitsTimer = null
}

watch(autoRefreshLocks, (enabled) => {
  if (enabled) startLockWaitsAutoRefresh()
  else stopLockWaitsAutoRefresh()
})

// Binary logs are server-wide, so only the scoped panels refetch.
watch(selectedSite, () => {
  loadProcesses()
  loadLockWaits()
  loadSize()
})

onUnmounted(stopLockWaitsAutoRefresh)

const load = async () => {
  loading.value = true
  error.value = ''
  if (route.query.site) selectedSite.value = String(route.query.site)
  try {
    const result = await databaseApi.diagnostics()
    if (result.error)
      throw new Error(apiErrorMessage(result, 'Could not load database diagnostics.'))
    diagnostics.value = result
    configuredEngine.value = result.engine
    if (!result.supported) return
    // The rest of the panels start collapsed and load themselves on first expand.
    await Promise.all([loadSites(), loadSize()])
  } catch (e) {
    error.value = e.message || 'Could not load database diagnostics.'
  } finally {
    loading.value = false
  }
}

const loadSites = async () => {
  try {
    const result = await databaseApi.sites()
    sites.value = Array.isArray(result) ? result : []
  } catch {
    sites.value = [] // Scoping is optional - the page still works server-wide.
  }
}

onMounted(load)
</script>

<template>
  <Teleport defer to="#header-actions">
    <Select
      v-if="siteOptions.length > 1"
      v-model="selectedSite"
      :options="siteOptions"
      class="w-32 sm:w-44"
    />
  </Teleport>

  <div class="p-3 md:p-4 flex flex-col gap-4">
    <LoadingText v-if="loading && !diagnostics" class="justify-center py-16" />

    <div
      v-else-if="diagnostics && !diagnostics.supported"
      class="flex flex-col items-center gap-1 bg-surface-white py-14 border rounded-6 border-outline-gray-2 text-center"
    >
      <span class="size-6 text-ink-gray-3 lucide-database" />
      <p class="font-medium text-ink-gray-7 text-sm">No database server</p>
      <p class="max-w-sm text-ink-gray-5 text-p-xs">{{ diagnostics.reason }}</p>
    </div>

    <ErrorMessage v-else-if="error" :message="error" />

    <template v-else-if="diagnostics">
      <DatabasePanel
        title="Database Size Breakup"
        subtitle="Analyze how storage is used"
        hide-chevron
        :badge="selectedSite ? scopeBadge : 'Server-wide'"
        :loading="sizeLoading"
        @refresh="loadSize"
      >
        <template v-if="selectedSite" #actions>
          <Button @click="showTableSizes = true">View Details</Button>
        </template>

        <ErrorMessage v-if="sizeError" :message="sizeError" class="m-4" />

        <div v-else-if="sizeLoading" class="px-4 pb-4">
          <Skeleton class="rounded-full w-full h-5" />
          <div v-for="row in 3" :key="row" class="flex justify-between gap-4 py-2">
            <Skeleton class="rounded-4 w-32 h-4" />
            <Skeleton class="rounded-4 w-16 h-4" />
          </div>
        </div>

        <p v-else-if="!size" class="py-10 border-t border-outline-gray-2 text-ink-gray-5 text-sm text-center">
          No results to display
        </p>

        <SizeBreakup v-else :size="size" />
      </DatabasePanel>

      <DatabasePanel
        title="Database Processes"
        subtitle="Analyze the processes of the database"
        :badge="scopeBadge"
        :loading="processesLoading"
        @open="loadProcesses"
        @refresh="loadProcesses"
      >
        <ErrorMessage v-if="processesError" :message="processesError" class="m-4" />
        <Table
          v-else-if="processRows.length"
          class="px-4 pb-4"
          :columns="processColumns"
          :rows="processRows"
        >
          <template #actions="{ row }">
            <Button variant="ghost" theme="red" iconLeft="lucide-x" @click="confirmKill(row.process)">
              Kill
            </Button>
          </template>
        </Table>

        <p v-else class="py-10 border-t border-outline-gray-2 text-ink-gray-5 text-sm text-center">No results to display</p>
      </DatabasePanel>

      <DatabasePanel
        title="Database Locks"
        subtitle="Analyze the lock waits of the database"
        :badge="[scopeBadge, lockColumnsBadge]"
        :loading="lockWaitsLoading"
        @open="openLockWaits"
        @refresh="loadLockWaits"
      >
        <template #actions>
          <label class="flex items-center gap-2 cursor-pointer">
            <Switch v-model="autoRefreshLocks" />
            <span class="text-ink-gray-7 text-sm">Auto Refresh</span>
          </label>
        </template>

        <ErrorMessage v-if="lockWaitsError" :message="lockWaitsError" class="m-4" />
        <Table v-else-if="lockRows.length" class="px-4 pb-4" :columns="lockColumns" :rows="lockRows" />

        <p v-else class="py-10 border-t border-outline-gray-2 text-ink-gray-5 text-sm text-center">No results to display</p>
      </DatabasePanel>

      <QueryAnalysisPanel
        :site="selectedSite"
        :badge="scopeBadge"
        :enabled="Boolean(diagnostics?.performance_schema_enabled)"
        :show-site="!selectedSite"
        :site-by-database="siteByDatabase"
      />

      <IndexAnalysisPanel
        :site="selectedSite"
        :badge="scopeBadge"
        :enabled="Boolean(diagnostics?.performance_schema_enabled)"
        :show-site="!selectedSite"
        :site-by-database="siteByDatabase"
      />

      <DatabasePanel
        v-if="hasBinlogs"
        title="Database Binary Logs"
        subtitle="Manage the binary logs of the database"
        :badge="selectedSite ? 'Server-wide' : ''"
        :loading="binlogsLoading"
        @open="loadBinlogs"
        @refresh="loadBinlogs"
      >
        <ErrorMessage v-if="binlogsError" :message="binlogsError" class="m-4" />

        <p
          v-else-if="!binlogRows.length"
          class="py-10 border-t border-outline-gray-2 text-ink-gray-5 text-sm text-center"
        >
          No results to display
        </p>

        <div v-else class="p-4">
          <Table :columns="binlogColumns" :rows="binlogRows">
            <template #selected="{ row }">
              <Checkbox
                :modelValue="row.index <= selectedIndex"
                :disabled="row.isActive"
                @update:modelValue="toggle(row.index, $event)"
              />
            </template>

            <template #actions="{ row }">
              <Tooltip v-if="!row.isActive" text="Delete this file and every older one">
                <Button
                  variant="ghost"
                  theme="red"
                  icon="lucide-trash-2"
                  label="Delete binary logs"
                  @click="confirmPurge(row.index)"
                />
              </Tooltip>
            </template>
          </Table>

          <div v-if="binlogs.length" class="flex flex-wrap justify-between items-center gap-2 mt-3">
            <p class="text-ink-gray-5 text-p-xs">
              The newest log is in use and cannot be deleted. Selecting a file also selects every
              older one, because the server can only purge them together.
            </p>

            <Button
              v-if="selectedIndex >= 0"
              variant="subtle"
              theme="red"
              size="sm"
              iconLeft="lucide-trash-2"
              @click="confirmPurge(selectedIndex)"
            >
              Delete {{ selectedIndex + 1 }} file{{ selectedIndex === 0 ? '' : 's' }}
            </Button>
          </div>
        </div>
      </DatabasePanel>
    </template>
  </div>

  <TableSizesDialog v-model:open="showTableSizes" :site="selectedSite" />

  <Dialog v-model="showKillDialog" title="Kill database process" size="sm">
    <p class="text-ink-gray-7 text-sm leading-relaxed">
      Close connection <strong>{{ killTarget?.id }}</strong> and roll back whatever it is running?
      Any bench sharing this server may own it.
    </p>

    <dl class="flex flex-col gap-2 mt-4 text-sm">
      <div
        v-for="item in killDetails"
        :key="item.label"
        class="flex justify-between items-baseline gap-4"
      >
        <dt class="text-ink-gray-5 shrink-0">{{ item.label }}</dt>
        <dd class="font-medium text-ink-gray-8 truncate">{{ item.value }}</dd>
      </div>
    </dl>

    <p v-if="killQuery" class="mt-4 mb-1.5 text-ink-gray-5 text-sm">Query</p>

    <pre
      v-if="killQuery"
      class="bg-surface-gray-2 p-3 rounded-4 max-h-40 overflow-auto font-mono text-ink-gray-8 text-sm leading-relaxed whitespace-pre-wrap break-words"
    >{{ killQuery }}</pre>

    <ErrorMessage v-if="killError" :message="killError" class="mt-4" />

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button @click="showKillDialog = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="killing" @click="kill">Kill process</Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showPurgeDialog" title="Delete binary logs" size="sm">
    <p class="text-ink-gray-7 text-sm">
      Permanently delete
      <strong
        >{{ pendingFiles.length }}
        file{{ pendingFiles.length === 1 ? '' : 's' }}</strong
      >, freeing {{ pendingSize }}? Binary logs are shared by every bench on this server and are
      used for point-in-time recovery and replication.
    </p>

    <dl class="space-y-1.5 bg-surface-gray-1 mt-3 p-3 rounded-6 text-xs">
      <div
        v-for="item in purgeDetails"
        :key="item.label"
        class="flex justify-between items-baseline gap-4"
      >
        <dt class="text-ink-gray-5 shrink-0">{{ item.label }}</dt>
        <dd class="font-mono font-medium text-ink-gray-8 truncate">{{ item.value }}</dd>
      </div>
    </dl>

    <ErrorMessage v-if="purgeError" :message="purgeError" class="mt-3" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showPurgeDialog = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="purging" @click="purge">Delete</Button>
      </div>
    </template>
  </Dialog>
</template>
