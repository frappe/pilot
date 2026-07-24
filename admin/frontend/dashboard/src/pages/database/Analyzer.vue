<template>
  <Teleport defer to="#header-actions">
    <div class="flex items-center gap-2">
      <FormControl
        v-if="siteOptions.length > 1"
        type="select"
        v-model="selectedSite"
        :options="siteOptions"
        class="w-32 sm:w-44"
      />
      <FormControl type="select" v-model="engine" :options="engineOptions" class="w-32 sm:w-40" />
    </div>
  </Teleport>

  <div class="flex flex-col gap-4">
    <div v-if="loading && !diagnostics" class="flex justify-center py-16">
      <LoadingText />
    </div>

    <div
      v-else-if="engine !== configuredEngine"
      class="flex flex-col items-center gap-1 bg-surface-white py-14 border rounded-lg border-outline-gray-2 text-center"
    >
      <span class="size-6 text-ink-gray-3 lucide-database-zap" />
      <p class="font-medium text-ink-gray-7 text-sm">Not configured on this bench</p>
      <p class="max-w-sm text-ink-gray-5 text-xs">
        This bench runs {{ engineLabel(configuredEngine) }}. Switch the selector back to
        {{ engineLabel(configuredEngine) }}
        to see its diagnostics.
      </p>
    </div>

    <div
      v-else-if="diagnostics && !diagnostics.supported"
      class="flex flex-col items-center gap-1 bg-surface-white py-14 border rounded-lg border-outline-gray-2 text-center"
    >
      <span class="size-6 text-ink-gray-3 lucide-database" />
      <p class="font-medium text-ink-gray-7 text-sm">No database server</p>
      <p class="max-w-sm text-ink-gray-5 text-xs">{{ diagnostics.reason }}</p>
    </div>

    <ErrorMessage v-else-if="error" :message="error" />

    <template v-else-if="diagnostics">
      <DatabasePanel
        title="Database Size Breakup"
        subtitle="Analyze how storage is used"
        :badge="selectedSite ? scopeBadge : 'Server-wide'"
        :loading="sizeLoading"
        @refresh="loadSize"
      >
        <template v-if="selectedSite" #actions>
          <Button variant="subtle" size="sm" @click="showTableSizes = true">View Details</Button>
        </template>
        <ErrorMessage v-if="sizeError" :message="sizeError" class="m-4" />
        <p v-else-if="!size" class="py-6 text-ink-gray-5 text-sm text-center">
          No results to display
        </p>
        <SizeBreakup v-else :size="size" />
      </DatabasePanel>

      <DatabasePanel
        title="Database Processes"
        subtitle="Analyze the processes of the database"
        :badge="scopeBadge"
        :loading="processesLoading"
        @refresh="loadProcesses"
      >
        <ErrorMessage v-if="processesError" :message="processesError" class="m-4" />
        <ListView
          v-else
          class="p-4 !w-full"
          :columns="processColumns"
          :rows="processRows"
          row-key="number"
          :options="{ selectable: false, showTooltip: false }"
        >
          <template #cell="{ column, row, item }">
            <div v-if="column.key === 'actions'" class="flex justify-end">
              <Button
                variant="ghost"
                theme="red"
                size="sm"
                iconLeft="lucide-x"
                @click="confirmKill(row.process)"
              >
                Kill
              </Button>
            </div>
            <ListRowItem v-else :column="column" :row="row" :item="item" :align="column.align" />
          </template>
          <ListHeader />
          <ListRows v-if="processRows.length" />
          <p v-else class="py-6 text-ink-gray-5 text-sm text-center">No results to display</p>
        </ListView>
      </DatabasePanel>

      <DatabasePanel
        title="Database Locks"
        subtitle="Analyze the lock waits of the database"
        :badge="[scopeBadge, lockColumnsBadge]"
        :loading="lockWaitsLoading"
        show-auto-refresh
        :auto-refresh="autoRefreshLocks"
        @update:auto-refresh="autoRefreshLocks = $event"
        @refresh="loadLockWaits"
      >
        <ErrorMessage v-if="lockWaitsError" :message="lockWaitsError" class="m-4" />
        <ListView
          v-else
          class="p-4 !w-full"
          :columns="lockColumns"
          :rows="lockRows"
          row-key="number"
          :options="{ selectable: false, showTooltip: false }"
        >
          <template #cell="{ column, row, item }">
            <ListRowItem :column="column" :row="row" :item="item" :align="column.align" />
          </template>
          <ListHeader />
          <ListRows v-if="lockRows.length" />
          <p v-else class="py-6 text-ink-gray-5 text-sm text-center">No results to display</p>
        </ListView>
      </DatabasePanel>

      <DatabasePanel
        title="Database Binary Logs"
        subtitle="Manage the binary logs of the database"
        :badge="selectedSite ? 'Server-wide' : ''"
        :loading="binlogsLoading"
        @refresh="loadBinlogs"
      >
        <ErrorMessage v-if="binlogsError" :message="binlogsError" class="m-4" />
        <div v-else class="p-4">
          <ListView
            class="!w-full"
            :columns="binlogColumns"
            :rows="binlogRows"
            row-key="number"
            :options="{ selectable: false, showTooltip: false }"
          >
            <template #cell="{ column, row, item }">
              <Checkbox
                v-if="column.key === 'selected'"
                :modelValue="row.index <= selectedIndex"
                :disabled="row.isActive"
                @update:modelValue="toggle(row.index, $event)"
              />
              <div v-else-if="column.key === 'actions'" class="flex justify-end">
                <Tooltip v-if="!row.isActive" text="Delete this file and every older one">
                  <Button variant="ghost" theme="red" size="sm" @click="confirmPurge(row.index)">
                    <span class="size-4 lucide-trash-2" />
                  </Button>
                </Tooltip>
              </div>
              <ListRowItem v-else :column="column" :row="row" :item="item" :align="column.align" />
            </template>
            <ListHeader />
            <ListRows v-if="binlogRows.length" />
            <p v-else class="py-6 text-ink-gray-5 text-sm text-center">No results to display</p>
          </ListView>

          <div v-if="binlogs.length" class="flex flex-wrap justify-between items-center gap-2 mt-3">
            <p class="text-ink-gray-5 text-xs">
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

  <Dialog v-model="showKillDialog" :options="{ title: 'Kill database process', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Close connection <strong>{{ killTarget?.Id }}</strong> and roll back whatever it is running?
        Any bench sharing this server may own it.
      </p>

      <dl class="space-y-1.5 bg-surface-gray-1 mt-3 p-3 rounded-lg text-xs">
        <div
          v-for="item in killDetails"
          :key="item.label"
          class="flex justify-between items-baseline gap-4"
        >
          <dt class="text-ink-gray-5 shrink-0">{{ item.label }}</dt>
          <dd class="font-medium text-ink-gray-8 truncate">{{ item.value }}</dd>
        </div>
        <div v-if="killQuery" class="space-y-1.5 pt-1.5 border-t border-outline-gray-2">
          <dt class="text-ink-gray-5">Query</dt>
          <dd class="max-h-24 overflow-y-auto font-mono font-medium text-ink-gray-8 break-all">
            {{ killQuery }}
          </dd>
        </div>
      </dl>

      <ErrorMessage v-if="killError" :message="killError" class="mt-3" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showKillDialog = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="killing" @click="kill">Kill process</Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showPurgeDialog" :options="{ title: 'Delete binary logs', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Permanently delete
        <strong
          >{{ pendingFiles.length }}
          file{{ pendingFiles.length === 1 ? '' : 's' }}</strong
        >, freeing {{ pendingSize }}? Binary logs are shared by every bench on this server and are
        used for point-in-time recovery and replication.
      </p>

      <dl class="space-y-1.5 bg-surface-gray-1 mt-3 p-3 rounded-lg text-xs">
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
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showPurgeDialog = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="purging" @click="purge">Delete</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  Button,
  Checkbox,
  Dialog,
  ErrorMessage,
  FormControl,
  ListHeader,
  ListRowItem,
  ListRows,
  ListView,
  LoadingText,
  Tooltip,
  toast,
} from 'frappe-ui'
import DatabasePanel from '@/components/database/DatabasePanel.vue'
import SizeBreakup from '@/components/database/SizeBreakup.vue'
import TableSizesDialog from '@/components/database/TableSizesDialog.vue'
import { apiErrorMessage } from '@/api/client'
import { databaseApi } from '@/api/database'
import { formatBytes } from '@/utils/format'
import { relativeTime } from '@/utils/taskFormat'

const AUTO_REFRESH_INTERVAL_MS = 2000

const processColumns = [
  { label: '#', key: 'number', align: 'left', width: '2.5rem' },
  { label: 'ID', key: 'id', align: 'left', width: 1 },
  { label: 'State', key: 'state', align: 'left', width: 1 },
  { label: 'Time', key: 'time', align: 'right', width: 1 },
  { label: 'User', key: 'user', align: 'left', width: 1 },
  { label: 'Host', key: 'host', align: 'left', width: 1.5 },
  { label: 'Command', key: 'command', align: 'left', width: 1 },
  { label: 'Query', key: 'query', align: 'left', width: 3 },
  { label: '', key: 'actions', align: 'right', width: '5rem' },
]

const lockColumns = [
  { label: '#', key: 'number', align: 'left', width: '2rem' },
  { label: 'ID', key: 'id', align: 'left', width: 0.8 },
  { label: 'Type', key: 'type', align: 'left', width: 0.8 },
  { label: 'Mode', key: 'mode', align: 'left', width: 0.8 },
  { label: 'Table', key: 'table', align: 'left', width: 1.2 },
  { label: 'Index', key: 'index', align: 'left', width: 0.8 },
  { label: 'State', key: 'state', align: 'left', width: 0.8 },
  { label: 'Started', key: 'started', align: 'left', width: 1.2 },
  { label: 'Query', key: 'query', align: 'left', width: 1.5 },
  { label: 'Rows Locked', key: 'rowsLocked', align: 'right', width: 0.9 },
  { label: 'Rows Modified', key: 'rowsModified', align: 'right', width: 1 },
]

const binlogColumns = [
  { label: '#', key: 'number', align: 'left', width: '2rem' },
  { label: '', key: 'selected', align: 'left', width: '2rem' },
  { label: 'File', key: 'name', align: 'left', width: 2 },
  { label: 'Date', key: 'date', align: 'left', width: 1.5 },
  { label: 'Size', key: 'size', align: 'right', width: 1 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const engineOptions = [
  { label: 'MariaDB', value: 'mariadb' },
  { label: 'PostgreSQL', value: 'postgres' },
]

const loading = ref(false)
const error = ref('')
const diagnostics = ref(null)
const engine = ref('mariadb')
const configuredEngine = ref('mariadb')
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
    id: process.Id,
    state: process.State || '—',
    time: `${process.Time}s`,
    user: process.User,
    host: process.Host || '—',
    command: process.Command,
    query: truncateQuery(process.Info),
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
    date: fileAge(file),
    size: formatBytes(file.size_bytes),
    index,
    isActive: index === binlogs.value.length - 1,
  })),
)

const killDetails = computed(() => {
  const process = killTarget.value
  if (!process) return []
  return [
    { label: 'User', value: process.User },
    { label: 'Database', value: process.db || '—' },
    { label: 'State', value: process.Command },
    { label: 'Running for', value: `${process.Time}s` },
  ]
})

const killQuery = computed(() => killTarget.value?.Info || '')

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
  engine.value === 'postgres' ? "Some columns aren't available for PostgreSQL" : '',
)

// Only sites on this server can be scoped to; a SQLite site owns a file, not a
// database on the selected engine.
const siteOptions = computed(() => [
  { label: 'All databases', value: '' },
  ...sites.value
    .filter((site) => site.db_type === configuredEngine.value)
    .map((site) => ({ label: site.name, value: site.name })),
])

const scopeBadge = computed(() => selectedSite.value)

function engineLabel(value) {
  return engineOptions.find((option) => option.value === value)?.label || value
}

const MAX_QUERY_LENGTH = 120

// Long queries can be arbitrarily large single-line strings that would
// otherwise force the table wider than the page.
function truncateQuery(query) {
  if (!query) return '—'
  return query.length > MAX_QUERY_LENGTH ? `${query.slice(0, MAX_QUERY_LENGTH)}…` : query
}

function fileAge(file) {
  return file.modified_ms ? relativeTime(new Date(file.modified_ms).toISOString()) : '—'
}

// Purging is contiguous from the oldest file, so ticking one file ticks every
// older file with it and unticking one clears everything newer.
function toggle(index, checked) {
  selectedIndex.value = checked ? index : index - 1
}

function confirmKill(process) {
  killTarget.value = process
  killError.value = ''
  showKillDialog.value = true
}

async function kill() {
  killing.value = true
  killError.value = ''
  try {
    const result = await databaseApi.killProcess(killTarget.value.Id)
    if (result.error) throw new Error(apiErrorMessage(result, 'Could not kill the process.'))
    showKillDialog.value = false
    toast.success(`Killed process ${killTarget.value.Id}`)
    await loadProcesses()
  } catch (e) {
    killError.value = e.message || 'Could not kill the process.'
  } finally {
    killing.value = false
  }
}

function confirmPurge(index) {
  pendingIndex.value = index
  purgeError.value = ''
  showPurgeDialog.value = true
}

async function purge() {
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

async function loadProcesses() {
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

async function loadLockWaits() {
  lockWaitsLoading.value = true
  lockWaitsError.value = ''
  try {
    const result = await databaseApi.lockWaitRows(selectedSite.value)
    if (result?.error)
      throw new Error(apiErrorMessage(result, 'Could not load database lock waits.'))
    lockWaits.value = Array.isArray(result) ? result : []
  } catch (e) {
    lockWaitsError.value = e.message || 'Could not load database lock waits.'
  } finally {
    lockWaitsLoading.value = false
  }
}

async function loadSize() {
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

async function loadBinlogs() {
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

function startLockWaitsAutoRefresh() {
  stopLockWaitsAutoRefresh()
  lockWaitsTimer = setInterval(loadLockWaits, AUTO_REFRESH_INTERVAL_MS)
}

function stopLockWaitsAutoRefresh() {
  if (lockWaitsTimer) clearInterval(lockWaitsTimer)
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

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await databaseApi.diagnostics()
    if (result.error)
      throw new Error(apiErrorMessage(result, 'Could not load database diagnostics.'))
    diagnostics.value = result
    configuredEngine.value = result.engine || 'mariadb'
    engine.value = configuredEngine.value
    if (!result.supported) return
    await Promise.all([loadSites(), loadSize(), loadProcesses(), loadLockWaits(), loadBinlogs()])
    if (autoRefreshLocks.value) startLockWaitsAutoRefresh()
  } catch (e) {
    error.value = e.message || 'Could not load database diagnostics.'
  } finally {
    loading.value = false
  }
}

async function loadSites() {
  try {
    const result = await databaseApi.sites()
    sites.value = Array.isArray(result) ? result : []
  } catch {
    sites.value = [] // Scoping is optional - the page still works server-wide.
  }
}

onMounted(load)
</script>

<style scoped>
/* A `1fr` grid track takes its minimum from the item's min-content width, so a
   long header label or query would widen the table past the panel and add a
   horizontal scrollbar. Letting the cells shrink keeps every column in view. */
:deep(.grid) > * {
  min-width: 0;
  overflow: hidden;
}
</style>
