<template>
  <Teleport defer to="#header-actions">
    <FormControl type="select" v-model="engine" :options="engineOptions" class="w-32 sm:w-40" />
  </Teleport>

  <div class="flex flex-col gap-4">
    <div class="flex justify-between items-center gap-3">
      <h2 class="font-semibold text-ink-gray-9 text-lg">Live database activity across every site</h2>
      <Button variant="ghost" size="sm" icon="lucide-refresh-cw" :loading="loading" @click="load" />
    </div>

    <div v-if="loading && !diagnostics" class="flex justify-center py-16">
      <LoadingText />
    </div>

    <div v-else-if="engine !== configuredEngine"
      class="flex flex-col items-center gap-1 bg-surface-white py-14 border rounded-lg border-outline-gray-2 text-center">
      <span class="size-6 text-ink-gray-3 lucide-database-zap" />
      <p class="font-medium text-ink-gray-7 text-sm">Not configured on this bench</p>
      <p class="max-w-sm text-ink-gray-5 text-xs">
        This bench runs {{ engineLabel(configuredEngine) }}. Switch the selector back to
        {{ engineLabel(configuredEngine) }} to see its diagnostics.
      </p>
    </div>

    <div v-else-if="engine === 'postgres'"
      class="flex flex-col items-center gap-1 bg-surface-white py-14 border rounded-lg border-outline-gray-2 text-center">
      <span class="size-6 text-ink-gray-3 lucide-clock" />
      <p class="font-medium text-ink-gray-7 text-sm">PostgreSQL diagnostics are coming</p>
      <p class="max-w-sm text-ink-gray-5 text-xs">
        Connections, locks and WAL for PostgreSQL benches are not implemented yet.
      </p>
    </div>

    <div v-else-if="diagnostics && !diagnostics.supported"
      class="flex flex-col items-center gap-1 bg-surface-white py-14 border rounded-lg border-outline-gray-2 text-center">
      <span class="size-6 text-ink-gray-3 lucide-database" />
      <p class="font-medium text-ink-gray-7 text-sm">No database server</p>
      <p class="max-w-sm text-ink-gray-5 text-xs">{{ diagnostics.reason }}</p>
    </div>

    <ErrorMessage v-else-if="error" :message="error" />

    <template v-else-if="diagnostics">
      <div class="gap-4 grid grid-cols-2 sm:grid-cols-3 bg-surface-white p-4 border rounded-lg border-outline-gray-2">
        <DiagnosticsStat icon="lucide-activity" :value="diagnostics.active_connections"
          :label="diagnostics.active_connections === 1 ? 'Connection' : 'Connections'" />
        <DiagnosticsStat icon="lucide-lock" :value="blockedCount" label="Blocked"
          :highlight="blockedCount > 0" />
        <DiagnosticsStat icon="lucide-scroll-text" :value="binlogSize" label="Binary logs" />
      </div>

      <p class="text-ink-gray-5 text-sm">Live</p>

      <DiagnosticsSection title="Database processes" icon="lucide-activity"
        :subtitle="processes.length ? `${processes.length}` : ''">
        <p v-if="!processes.length" class="py-3 text-ink-gray-5 text-sm">No active processes.</p>
        <ListView v-else class="pt-3" :columns="processColumns" :rows="processRows" row-key="id"
          :options="{ selectable: false, showTooltip: false }">
          <template #cell="{ column, row, item }">
            <div v-if="column.key === 'actions'" class="flex justify-end">
              <Button variant="ghost" theme="red" size="sm" @click="confirmKill(row.process)">Kill</Button>
            </div>
            <ListRowItem v-else :column="column" :row="row" :item="item" :align="column.align" />
          </template>
        </ListView>
      </DiagnosticsSection>

      <DiagnosticsSection title="Database locks" icon="lucide-lock">
        <dl class="gap-4 grid grid-cols-2 sm:grid-cols-3 pt-3">
          <div v-for="item in lockItems" :key="item.label">
            <dt class="text-ink-gray-5 text-xs">{{ item.label }}</dt>
            <dd class="font-medium text-ink-gray-8 text-base">{{ item.value }}</dd>
          </div>
        </dl>
      </DiagnosticsSection>

      <p class="mt-1 text-ink-gray-5 text-sm">History and maintenance</p>

      <DiagnosticsSection title="Database binary logs" icon="lucide-scroll-text"
        :subtitle="binlogs.length ? `${binlogs.length} file${binlogs.length === 1 ? '' : 's'}` : ''">
        <p v-if="!binlogs.length" class="py-3 text-ink-gray-5 text-sm">
          Binary logging is off, so there are no files to show.
        </p>
        <div v-else class="pt-3">
          <ListView :columns="binlogColumns" :rows="binlogRows" row-key="name"
            :options="{ selectable: false, showTooltip: false }">
            <template #cell="{ column, row, item }">
              <Checkbox v-if="column.key === 'selected'" :modelValue="row.index <= selectedIndex"
                :disabled="row.isActive" @update:modelValue="toggle(row.index, $event)" />
              <div v-else-if="column.key === 'actions'" class="flex justify-end">
                <Tooltip v-if="!row.isActive" text="Delete this file and every older one">
                  <Button variant="ghost" theme="red" size="sm" @click="confirmPurge(row.index)">
                    <span class="size-4 lucide-trash-2" />
                  </Button>
                </Tooltip>
              </div>
              <ListRowItem v-else :column="column" :row="row" :item="item" :align="column.align" />
            </template>
          </ListView>

          <div class="flex flex-wrap justify-between items-center gap-2 mt-3">
            <p class="text-ink-gray-5 text-xs">
              The newest log is in use and cannot be deleted. Selecting a file also selects every
              older one, because the server can only purge them together.
            </p>
            <Button v-if="selectedIndex >= 0" variant="subtle" theme="red" size="sm"
              iconLeft="lucide-trash-2" @click="confirmPurge(selectedIndex)">
              Delete {{ selectedIndex + 1 }} file{{ selectedIndex === 0 ? '' : 's' }}
            </Button>
          </div>
        </div>
      </DiagnosticsSection>
    </template>
  </div>

  <Dialog v-model="showKillDialog" :options="{ title: 'Kill database process', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Close connection <strong>{{ killTarget?.Id }}</strong> and roll back whatever it is running?
        Any bench sharing this server may own it.
      </p>

      <dl class="space-y-1.5 bg-surface-gray-1 mt-3 p-3 rounded-lg text-xs">
        <div v-for="item in killDetails" :key="item.label" class="flex justify-between items-baseline gap-4">
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
        Permanently delete <strong>{{ pendingFiles.length }}
        file{{ pendingFiles.length === 1 ? '' : 's' }}</strong>, freeing {{ pendingSize }}? Binary
        logs are shared by every bench on this server and are used for point-in-time recovery and
        replication.
      </p>

      <dl class="space-y-1.5 bg-surface-gray-1 mt-3 p-3 rounded-lg text-xs">
        <div v-for="item in purgeDetails" :key="item.label" class="flex justify-between items-baseline gap-4">
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
import { computed, onMounted, ref } from 'vue'
import {
  Button,
  Checkbox,
  Dialog,
  ErrorMessage,
  FormControl,
  ListRowItem,
  ListView,
  LoadingText,
  Tooltip,
  toast,
} from 'frappe-ui'
import DiagnosticsSection from '@/components/database/DiagnosticsSection.vue'
import DiagnosticsStat from '@/components/database/DiagnosticsStat.vue'
import { apiErrorMessage } from '@/api/client'
import { databaseApi } from '@/api/database'
import { formatBytes } from '@/utils/format'
import { relativeTime } from '@/utils/taskFormat'

const processColumns = [
  { label: 'ID', key: 'id', align: 'left', width: 1 },
  { label: 'Command', key: 'command', align: 'left', width: 1 },
  { label: 'User', key: 'user', align: 'left', width: 1 },
  { label: 'Database', key: 'database', align: 'left', width: 1.5 },
  { label: 'Query', key: 'query', align: 'left', width: 3 },
  { label: 'Time', key: 'time', align: 'right', width: 1 },
  { label: '', key: 'actions', align: 'right', width: '4rem' },
]

const binlogColumns = [
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
const processes = ref([])
const binlogs = ref([])
const engine = ref('mariadb')
const configuredEngine = ref('mariadb')

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
  processes.value.map((process) => ({
    id: process.Id,
    command: process.Command,
    user: process.User,
    database: process.db || '—',
    query: process.Info || '—',
    time: `${process.Time}s`,
    process,
  })),
)

const binlogRows = computed(() =>
  binlogs.value.map((file, index) => ({
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

const blockedCount = computed(() => diagnostics.value?.lock_waits?.current_waits ?? 0)
const binlogSize = computed(() => formatBytes(diagnostics.value?.binlog?.size_bytes ?? 0))

const lockItems = computed(() => {
  const waits = diagnostics.value?.lock_waits || {}
  return [
    { label: 'Waiting now', value: waits.current_waits ?? '—' },
    { label: 'Total waits', value: waits.total_waits ?? '—' },
    { label: 'Wait timeout', value: waits.timeout_seconds == null ? '—' : `${waits.timeout_seconds}s` },
  ]
})

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

function engineLabel(value) {
  return engineOptions.find((option) => option.value === value)?.label || value
}

// The newest log is the one being written to; the server refuses to purge it.
function isActiveLog(index) {
  return index === binlogs.value.length - 1
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
    await load()
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
    await load()
  } catch (e) {
    purgeError.value = e.message || 'Could not delete binary logs.'
  } finally {
    purging.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await databaseApi.diagnostics()
    if (result.error) throw new Error(apiErrorMessage(result, 'Could not load database diagnostics.'))
    diagnostics.value = result
    configuredEngine.value = result.engine || 'mariadb'
    engine.value = configuredEngine.value
    if (!result.supported) return
    const [processResult, binlogResult] = await Promise.all([
      databaseApi.processList(),
      databaseApi.binlogs.list(),
    ])
    processes.value = Array.isArray(processResult) ? processResult : []
    binlogs.value = Array.isArray(binlogResult) ? binlogResult : []
    selectedIndex.value = -1
  } catch (e) {
    error.value = e.message || 'Could not load database diagnostics.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
