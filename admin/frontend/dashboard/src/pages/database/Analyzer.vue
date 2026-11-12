<script setup lang="ts">
import {
  Button,
  Checkbox,
  Dialog,
  ErrorMessage,
  LoadingText,
  Select,
  Switch,
  TabList,
  TabTrigger,
  Tabs,
  Tooltip,
  toast,
} from 'frappe-ui'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage, hasApiError } from '@/api/client'
import { databaseApi } from '@/api/database'
import EmptyState from '@/components/common/EmptyState.vue'
import Table from '@/components/common/Table.vue'
import DatabasePanel from '@/components/database/DatabasePanel.vue'
import IndexAnalysisPanel from '@/components/database/IndexAnalysisPanel.vue'
import DeadlocksPanel from '@/components/database/DeadlocksPanel.vue'
import QueryAnalysisPanel from '@/components/database/QueryAnalysisPanel.vue'
import SizeOverview from '@/components/database/SizeOverview.vue'
import type {
  BinlogFile,
  DatabaseDiagnostics,
  DatabaseProcess,
  DatabaseSite,
  DatabaseSize,
  LockWaitRow,
  UnsupportedDatabaseDiagnostics,
} from '@/types/database'
import { errorMessage } from '@/utils/error'
import { formatBytes } from '@/utils/format'
import { relativeTime } from '@/utils/time'

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
  { label: 'ID', key: 'id', class: 'w-[7%]' },
  { label: 'Type', key: 'type', class: 'w-[7%]' },
  { label: 'Mode', key: 'mode', class: 'w-[6%]' },
  { label: 'Table', key: 'table', class: 'w-[11%]' },
  { label: 'Index', key: 'index', class: 'w-[7%]' },
  { label: 'Started', key: 'started', class: 'w-[11%]' },
  { label: 'Query', key: 'query', class: 'w-[15%]' },
  { label: 'Locked', key: 'rowsLocked', class: 'w-[6%]' },
  { label: 'Modified', key: 'rowsModified', class: 'w-[6%]' },
  { label: 'Blocked by', key: 'blocker' },
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
const router = useRouter()

const picked = ref(String(route.params.tab || 'size'))
const openedSections = new Set<string>()

const loading = ref(false)
const error = ref('')
const diagnostics = ref<DatabaseDiagnostics | UnsupportedDatabaseDiagnostics | null>(null)
const configuredEngine = ref('')
const sites = ref<DatabaseSite[]>([])
const selectedSite = ref('')

const processes = ref<DatabaseProcess[]>([])
const processesLoading = ref(false)
const processesError = ref('')

const lockWaits = ref<LockWaitRow[]>([])
const lockWaitsLoading = ref(false)
const lockWaitsError = ref('')
const autoRefreshLocks = ref(true)
let lockWaitsTimer: ReturnType<typeof setTimeout> | null = null
let lockWaitsPollVersion = 0
let lockWaitsRequest: Promise<void> | null = null
let lockWaitsRequestSite: string | null = null
let lockWaitsReloadQueued = false

const binlogs = ref<BinlogFile[]>([])
const binlogsLoading = ref(false)
const binlogsError = ref('')

const size = ref<DatabaseSize | null>(null)
const sizeLoading = ref(false)
const sizeError = ref('')

const killTarget = ref<DatabaseProcess | null>(null)
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
    started: row.started || '—',
    query: truncateQuery(row.query),
    rowsLocked: row.rows_locked ?? '—',
    rowsModified: row.rows_modified ?? '—',
    blocker: row.blocking_thread_id ? blockingProcess(row) : null,
    blockerQuery: truncateQuery(row.blocking_query) || 'Idle in transaction',
  })),
)

// The blocker is often idle inside an open transaction, so it has no query; killing it ends the wait.
const blockingProcess = (row: LockWaitRow): DatabaseProcess => ({
  id: row.blocking_thread_id as number,
  user: null,
  host: null,
  database: null,
  command: null,
  state: null,
  duration_seconds: null,
  query: row.blocking_query,
})

const binlogRows = computed(() =>
  binlogs.value.map((file, index) => ({
    number: index + 1,
    name: file.name,
    date: relativeTime(file.modified_ms ?? 0),
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
const hasBinlogs = computed(() => {
  const current = diagnostics.value
  return current?.supported === true && Boolean(current.binlog)
})

// Only sites on this server can be scoped to; a SQLite site owns a file, not a
// database on the bench's engine.
const siteOptions = computed(() => [
  { label: 'All databases', value: '' },
  ...sites.value
    .filter((site) => site.db_type === configuredEngine.value)
    .map((site) => ({ label: site.name, value: site.name })),
])

const scopeBadge = computed(() => selectedSite.value)

const sections = computed(() => [
  { value: 'size', label: 'Size', iconLeft: 'lucide-hard-drive' },
  { value: 'processes', label: 'Processes', iconLeft: 'lucide-activity' },
  { value: 'locks', label: 'Locks', iconLeft: 'lucide-lock' },
  ...(configuredEngine.value === 'mariadb'
    ? [{ value: 'deadlocks', label: 'Deadlocks', iconLeft: 'lucide-swords' }]
    : []),
  { value: 'queries', label: 'Queries', iconLeft: 'lucide-scan-search' },
  { value: 'indexes', label: 'Indexes', iconLeft: 'lucide-list-tree' },
  ...(hasBinlogs.value
    ? [{ value: 'binlogs', label: 'Binary logs', iconLeft: 'lucide-scroll-text' }]
    : []),
])

const siteByDatabase = computed(() =>
  Object.fromEntries(
    sites.value.filter((site) => site.db_name).map((site) => [site.db_name, site.name]),
  ),
)

const MAX_QUERY_LENGTH = 30

// Long queries can be arbitrarily large single-line strings that would
// otherwise force the table wider than the page.
const truncateQuery = (query: string | null) => {
  if (!query) return '—'
  return query.length > MAX_QUERY_LENGTH ? `${query.slice(0, MAX_QUERY_LENGTH)}…` : query
}

const formatSeconds = (seconds: number | null) => {
  return seconds == null ? '—' : `${Math.round(seconds)}s`
}

// Purging is contiguous from the oldest file, so ticking one file ticks every
// older file with it and unticking one clears everything newer.
const toggle = (index: number, checked: boolean) => {
  selectedIndex.value = checked ? index : index - 1
}

const confirmKill = (process: DatabaseProcess) => {
  killTarget.value = process
  killError.value = ''
  showKillDialog.value = true
}

const kill = async () => {
  const target = killTarget.value
  if (!target) return

  killing.value = true
  killError.value = ''
  try {
    const result = await databaseApi.killProcess(target.id)
    if (hasApiError(result)) throw new Error(apiErrorMessage(result, 'Could not kill the process.'))
    showKillDialog.value = false
    toast.success(`Killed process ${target.id}`)
    await (picked.value === 'locks' ? loadLockWaits() : loadProcesses())
  } catch (caught) {
    killError.value = errorMessage(caught, 'Could not kill the process.')
  } finally {
    killing.value = false
  }
}

const confirmPurge = (index: number) => {
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
    if (hasApiError(result)) {
      throw new Error(apiErrorMessage(result, 'Could not delete binary logs.'))
    }

    showPurgeDialog.value = false
    selectedIndex.value = -1
    toast.success('Binary logs deleted')
    await loadBinlogs()
  } catch (caught) {
    purgeError.value = errorMessage(caught, 'Could not delete binary logs.')
  } finally {
    purging.value = false
  }
}

const loadProcesses = async () => {
  processesLoading.value = true
  processesError.value = ''
  try {
    const result = await databaseApi.processList(selectedSite.value)
    if (hasApiError(result)) {
      throw new Error(apiErrorMessage(result, 'Could not load database processes.'))
    }

    processes.value = Array.isArray(result) ? result : []
  } catch (caught) {
    processesError.value = errorMessage(caught, 'Could not load database processes.')
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
    if (hasApiError(result)) {
      throw new Error(apiErrorMessage(result, 'Could not load database lock waits.'))
    }

    lockWaits.value = Array.isArray(result) ? result : []
    // Auto refresh only hunts for a lock wait; once one shows, keep it on screen.
    if (lockWaits.value.length) autoRefreshLocks.value = false
  } catch (caught) {
    if (site !== selectedSite.value) lockWaitsReloadQueued = true
    else lockWaitsError.value = errorMessage(caught, 'Could not load database lock waits.')
  }
}

const loadSize = async () => {
  sizeLoading.value = true
  sizeError.value = ''
  try {
    const result = await databaseApi.size(selectedSite.value)
    if (hasApiError(result)) {
      throw new Error(apiErrorMessage(result, 'Could not read the database size.'))
    }

    size.value = result
  } catch (caught) {
    size.value = null
    sizeError.value = errorMessage(caught, 'Could not read the database size.')
  } finally {
    sizeLoading.value = false
  }
}

const loadBinlogs = async () => {
  binlogsLoading.value = true
  binlogsError.value = ''
  try {
    const result = await databaseApi.binlogs.list()
    if (hasApiError(result)) throw new Error(apiErrorMessage(result, 'Could not load binary logs.'))
    binlogs.value = Array.isArray(result) ? result : []
    selectedIndex.value = -1
  } catch (caught) {
    binlogsError.value = errorMessage(caught, 'Could not load binary logs.')
  } finally {
    binlogsLoading.value = false
  }
}

const pollLockWaits = async (version: number) => {
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

watch(
  picked,
  (section, previous) => {
    openedSections.add(section)

    if (section !== String(route.params.tab || 'size')) {
      router.replace({
        params: { tab: section === 'size' ? '' : section },
        query: route.query,
      })
    }

    if (previous === 'locks') stopLockWaitsAutoRefresh()
    if (section === 'locks' && autoRefreshLocks.value) startLockWaitsAutoRefresh()
  },
  { immediate: true },
)

watch(
  () => String(route.params.tab || 'size'),
  (section) => (picked.value = section),
)

watch(
  () => String(route.query.site ?? ''),
  (site) => (selectedSite.value = site),
)

// Binary logs are server-wide, so only the scoped panels refetch, and only once opened.
watch(selectedSite, (site) => {
  if (site !== String(route.query.site ?? '')) {
    router.push({ query: { ...route.query, site: site || undefined } })
  }

  loadSize()

  if (openedSections.has('processes')) loadProcesses()
  if (openedSections.has('locks')) loadLockWaits()
})

onUnmounted(stopLockWaitsAutoRefresh)

const load = async () => {
  loading.value = true
  error.value = ''
  if (route.query.site) selectedSite.value = String(route.query.site)
  try {
    const result = await databaseApi.diagnostics()
    if (hasApiError(result)) {
      throw new Error(apiErrorMessage(result, 'Could not load database diagnostics.'))
    }

    diagnostics.value = result
    configuredEngine.value = result.engine
    if (!result.supported) return
    // The rest of the sections load themselves when first picked.
    await Promise.all([loadSites(), loadSize()])
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not load database diagnostics.')
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
      side="bottom"
      v-if="siteOptions.length > 1"
      v-model="selectedSite"
      :options="siteOptions"
      class="w-32 sm:w-44"
    />
  </Teleport>

  <LoadingText v-if="loading && !diagnostics" class="justify-center py-16" />

  <div
    v-else-if="diagnostics && !diagnostics.supported"
    class="flex flex-col items-center gap-1 bg-surface-white m-3 md:m-4 py-14 border rounded-6 border-outline-gray-2 text-center"
  >
    <span class="size-6 text-ink-gray-3 lucide-database" />
    <p class="font-medium text-ink-gray-7 text-sm">No database server</p>
    <p class="max-w-sm text-ink-gray-5 text-p-xs">{{ diagnostics.reason }}</p>
  </div>

  <ErrorMessage v-else-if="error" :message="error" class="m-3 md:m-4" />

  <template v-else-if="diagnostics">
    <Tabs v-model="picked">
      <TabList variant="underline" size="md" class="px-3 md:px-4">
        <TabTrigger
          v-for="section in sections"
          :key="section.value"
          :value="section.value"
          :label="section.label"
          :icon-left="section.iconLeft"
          class="py-1.5"
        />

        <div id="analyzer-actions" class="flex items-center gap-3 ml-auto shrink-0" />
      </TabList>
    </Tabs>

    <SizeOverview
      v-if="diagnostics.supported"
      v-show="picked === 'size'"
      :size="size"
      :loading="sizeLoading"
      :error="sizeError"
      :diagnostics="diagnostics"
      :site-by-database="siteByDatabase"
      :site="selectedSite"
      @refresh="loadSize"
    />

    <main v-show="picked !== 'size'" class="md:h-[calc(100dvh-93px)] p-3 md:p-4">
      <DatabasePanel
        :active="picked === 'processes'"
        :badge="scopeBadge"
        :loading="processesLoading"
        @open="loadProcesses"
        @refresh="loadProcesses"
      >
        <ErrorMessage v-if="processesError" :message="processesError" />

        <Table
          v-else-if="processRows.length"
          :columns="processColumns"
          :rows="processRows"
          height="flex-1"
         
        >
          <template #actions="{ row }">
            <Button
              variant="ghost"
              iconLeft="lucide-x"
              class="!text-ink-gray-5 hover:!text-ink-red-6"
              @click="confirmKill(row.process)"
            >
              Kill
            </Button>
          </template>
        </Table>

        <EmptyState
          v-else
          icon="lucide-activity"
          title="No processes"
          :description="selectedSite ? 'Nothing is connected to this database' : 'Nothing is connected to the server'"
          :bordered="false"
          class="flex-1 justify-center pb-16"
        />
      </DatabasePanel>

      <DatabasePanel
        :active="picked === 'locks'"
        :badge="[scopeBadge, lockColumnsBadge]"
        :loading="lockWaitsLoading"
        @open="loadLockWaits"
        @refresh="loadLockWaits"
      >
        <template #actions>
          <label class="flex items-center gap-2 cursor-pointer">
            <Switch v-model="autoRefreshLocks" />
            <span class="text-ink-gray-7 text-sm">Auto Refresh</span>
          </label>
        </template>

        <ErrorMessage v-if="lockWaitsError" :message="lockWaitsError" />

        <Table
          v-else-if="lockRows.length"
          :columns="lockColumns"
          :rows="lockRows"
          height="flex-1"
        >
          <template #blocker="{ row }">
            <div v-if="row.blocker" class="flex items-center gap-2 min-w-0">
              <span class="truncate" :title="row.blocker.query || ''">
                {{ row.blocker.id }} · {{ row.blockerQuery }}
              </span>
              <Button
                variant="ghost"
                iconLeft="lucide-x"
                class="!text-ink-gray-5 hover:!text-ink-red-6 shrink-0"
                @click="confirmKill(row.blocker)"
              >
                Kill
              </Button>
            </div>
            <span v-else>—</span>
          </template>
        </Table>

        <EmptyState
          v-else
          icon="lucide-lock"
          title="No lock waits"
          description="No query is waiting on a lock"
          :bordered="false"
          class="flex-1 justify-center pb-16"
        />
      </DatabasePanel>

      <DeadlocksPanel
        v-if="configuredEngine === 'mariadb'"
        :active="picked === 'deadlocks'"
        :site="selectedSite"
        :badge="scopeBadge"
        :show-site="!selectedSite"
        :site-by-database="siteByDatabase"
      />

      <QueryAnalysisPanel
        :active="picked === 'queries'"
        :site="selectedSite"
        :badge="scopeBadge"
        :enabled="Boolean(diagnostics?.performance_schema_enabled)"
        :show-site="!selectedSite"
        :site-by-database="siteByDatabase"
      />

      <IndexAnalysisPanel
        :active="picked === 'indexes'"
        :site="selectedSite"
        :badge="scopeBadge"
        :enabled="Boolean(diagnostics?.performance_schema_enabled)"
        :show-site="!selectedSite"
        :site-by-database="siteByDatabase"
      />

      <DatabasePanel
        v-if="hasBinlogs"
        :active="picked === 'binlogs'"
        :badge="selectedSite ? 'Server-wide' : ''"
        :loading="binlogsLoading"
        @open="loadBinlogs"
        @refresh="loadBinlogs"
      >
        <ErrorMessage v-if="binlogsError" :message="binlogsError" />

        <EmptyState
          v-else-if="!binlogRows.length"
          icon="lucide-scroll-text"
          title="No binary logs"
          description="The server has no binary log files"
          :bordered="false"
          class="flex-1 justify-center pb-16"
        />

        <template v-else>
          <Table :columns="binlogColumns" :rows="binlogRows" height="flex-1">
            <template #selected="{ row }">
              <Checkbox
                :modelValue="row.index <= selectedIndex"
                :disabled="row.isActive"
                @update:modelValue="toggle(row.index, Boolean($event))"
              />
            </template>

            <template #actions="{ row }">
              <Tooltip v-if="!row.isActive" text="Delete this file and every older one">
                <Button
                  variant="ghost"
                  icon="lucide-trash-2"
                  label="Delete binary logs"
                  class="!text-ink-gray-5 hover:!text-ink-red-6"
                  @click="confirmPurge(row.index)"
                />
              </Tooltip>
            </template>
          </Table>

          <div class="flex flex-wrap justify-between items-center gap-2">
            <p class="text-ink-gray-5 text-p-xs">
              The newest log is in use. Selecting a file also selects every older one.
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
        </template>
      </DatabasePanel>
    </main>
  </template>

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
