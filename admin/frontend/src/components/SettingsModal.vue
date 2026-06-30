<script setup>
import { ref, computed, watch } from 'vue'
import { Button, FormControl, ErrorMessage, LoadingText, Select, Badge, useTheme, Dialog, TextInput } from 'frappe-ui'
import LucideX from '~icons/lucide/x'
import { useTaskProgress } from '../composables/useTaskProgress.js'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue'])
const { watchTask } = useTaskProgress()

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const { currentTheme, setTheme } = useTheme()
const theme = computed({
  get: () => currentTheme.value,
  set: (val) => setTheme(val),
})
const THEME_OPTIONS = [
  { label: 'Light', value: 'light' },
  { label: 'Dark', value: 'dark' },
  { label: 'Auto (System)', value: 'system' },
]

const BASE_TABS = [
  { key: 'bench', label: 'Bench' },
  { key: 'apps', label: 'Apps' },
  { key: 'appearance', label: 'Appearance' },
  { key: 'redis', label: 'Redis' },
  { key: 'workers', label: 'Workers' },
  { key: 'monitoring', label: 'Monitoring' },
  { key: 'updates', label: 'Updates' },
]
const isLinux = ref(false)
const TABS = computed(() => {
  const dbType = form.value?.bench?.db_type
  const dbTab = dbType === 'postgres'
    ? { key: 'postgres', label: 'PostgreSQL' }
    : dbType === 'sqlite' ? null
    : { key: 'mariadb', label: 'MariaDB' }
  const tabs = [...BASE_TABS.slice(0, 3)]
  if (dbTab) tabs.push(dbTab)
  tabs.push(...BASE_TABS.slice(3))
  if (isLinux.value) tabs.push({ key: 'volume', label: 'ZFS Volume' })
  return tabs
})

const activeTab = ref('bench')

const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')
const saveSuccess = ref('')

const form = ref(null)
const originalProcessManager = ref(null)

const dbEngineLabel = computed(() => {
  const t = form.value?.bench?.db_type
  if (t === 'postgres') return 'PostgreSQL'
  if (t === 'sqlite') return 'SQLite'
  return 'MariaDB'
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch('/api/settings/')
    if (!res.ok) throw new Error(`${res.status}`)
    const data = await res.json()
    isLinux.value = data.is_linux === true
    if (Array.isArray(data.workers))
      data.workers = data.workers.map(g => ({ queues: (g.queues || []).join(', '), count: g.count }))
    // root_password is write-only — keep it blank so an untouched save preserves it
    // Degrade gracefully if an older backend omits the postgres or monitor blocks.
    data.postgres = { host: 'localhost', port: 5432, admin_user: 'postgres', password_set: false, ...(data.postgres || {}), root_password: '' }
    data.monitor = { system_log_path: '', log_path: '', system_log_max_size: '500M', application_log_max_size: '500M', ...(data.monitor || {}) }
    form.value = data
    originalProcessManager.value = data.production?.process_manager ?? 'none'
  } catch (e) {
    loadError.value = e.message
  } finally {
    loading.value = false
  }
}

function queueList(q) {
  if (Array.isArray(q)) return q.map(s => String(s).trim()).filter(Boolean)
  return String(q || '').split(',').map(s => s.trim()).filter(Boolean)
}

function addWorkerGroup() {
  form.value.workers.push({ queues: '', count: 1 })
}

function removeWorkerGroup(i) {
  form.value.workers.splice(i, 1)
}

function validateSettings() {
  const ports = [
    [form.value.bench.http_port, 'HTTP Port'],
    [form.value.bench.socketio_port, 'SocketIO Port'],
    [form.value.redis.cache_port, 'Redis Cache Port'],
    [form.value.redis.queue_port, 'Redis Queue Port'],
  ]
  for (const [port, name] of ports) {
    const n = Number(port)
    if (!Number.isInteger(n) || n < 1 || n > 65535)
      return `${name} must be between 1 and 65535.`
  }
  if (!Array.isArray(form.value.workers) || form.value.workers.length === 0)
    return 'Add at least one worker group.'
  for (const [i, group] of form.value.workers.entries()) {
    if (!queueList(group.queues).length)
      return `Worker group ${i + 1} needs at least one queue.`
    const n = Number(group.count)
    if (!Number.isInteger(n) || n < 1)
      return `Worker group ${i + 1} count must be at least 1.`
  }
  return null
}

async function save() {
  saveError.value = ''
  saveSuccess.value = ''
  const validationError = validateSettings()
  if (validationError) { saveError.value = validationError; return }
  saving.value = true
  try {
    const res = await fetch('/api/settings/', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value),
    })
    const d = await res.json()
    if (!d.ok) { saveError.value = d.error; return }

    const pm = form.value.production.process_manager
    const pmChanged = pm !== originalProcessManager.value
    const setupCommand = pmChanged && pm !== 'none' ? 'setup-production' : null

    if (setupCommand) {
      const taskRes = await fetch('/api/tasks/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: setupCommand }),
      })
      const taskData = await taskRes.json()
      if (taskData.ok) {
        show.value = false
        watchTask(taskData.task_id)
        return
      }
      saveError.value = taskData.error || 'Setup failed to start'
      return
    }

    saveSuccess.value = d.restarted ? 'Saved & restarted' : 'Saved'
    setTimeout(() => { saveSuccess.value = '' }, 3000)
    if (d.restart_error) saveError.value = `Restart failed: ${d.restart_error}`
  } catch (e) {
    saveError.value = e.message
  } finally {
    saving.value = false
  }
}

const cliUpdate = ref(null)
const checkingUpdate = ref(false)
const checkUpdateError = ref('')
const showUpdateDetails = ref(false)

async function checkCliUpdate() {
  checkingUpdate.value = true
  checkUpdateError.value = ''
  try {
    const res = await fetch('/api/updates/cli?fetch=1')
    if (!res.ok) throw new Error(`${res.status}`)
    cliUpdate.value = await res.json()
  } catch (e) {
    checkUpdateError.value = e.message
  } finally {
    checkingUpdate.value = false
  }
}

async function updateCli() {
  try {
    const res = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'update-cli' }),
    })
    const d = await res.json()
    if (d.ok) {
      show.value = false
      watchTask(d.task_id)
    } else {
      checkUpdateError.value = d.error
    }
  } catch (e) {
    checkUpdateError.value = e.message
  }
}

// Monitoring tab
const monitorStatus = ref(null)
const monitorStatusLoading = ref(false)

async function loadMonitorStatus() {
  monitorStatusLoading.value = true
  monitorStatus.value = null
  try {
    const res = await fetch('/api/monitor-status')
    if (res.ok) monitorStatus.value = await res.json()
  } finally {
    monitorStatusLoading.value = false
  }
}

function isRecentlyModified(isoString) {
  return (Date.now() - new Date(isoString).getTime()) < 30_000
}

function formatRelativeTime(isoString) {
  const seconds = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000)
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

// Apps tab
const benchApps = ref([])
const appRegistry = ref([])
const appsLoading = ref(false)
const appsError = ref('')
const appSearch = ref('')

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']
function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

const appLogoMap = computed(() =>
  Object.fromEntries(appRegistry.value.filter(a => a?.name).map(a => [a.name, a.logo_url]))
)
const appTitleMap = computed(() =>
  Object.fromEntries(appRegistry.value.filter(a => a?.name).map(a => [a.name, a.title]))
)

const filteredApps = computed(() => {
  const q = appSearch.value.toLowerCase().trim()
  const apps = benchApps.value.filter(a => a?.name)
  if (!q) return apps
  return apps.filter(a =>
    a.name.toLowerCase().includes(q) ||
    (appTitleMap.value[a.name] || '').toLowerCase().includes(q)
  )
})

async function loadBenchApps() {
  appsLoading.value = true
  appsError.value = ''
  try {
    const [appsRes, regRes] = await Promise.all([
      fetch('/api/apps/'),
      fetch('/api/apps/marketplace'),
    ])
    const appsData = await appsRes.json()
    const regData = await regRes.json()
    benchApps.value = Array.isArray(appsData) ? appsData : []
    appRegistry.value = Array.isArray(regData) ? regData : []
  } catch (e) {
    appsError.value = e.message
  } finally {
    appsLoading.value = false
  }
}

watch(activeTab, (tab) => {
  if (tab === 'apps') loadBenchApps()
  if (tab === 'monitoring') loadMonitorStatus()
})

watch(() => props.modelValue, (val) => {
  if (val) {
    activeTab.value = 'bench'
    saveError.value = ''
    saveSuccess.value = ''
    cliUpdate.value = null
    showUpdateDetails.value = false
    benchApps.value = []
    monitorStatus.value = null
    load()
  }
})
</script>

<template>
  <Dialog v-model="show" :options="{ size: '3xl' }">
    <template #body>
      <div class="flex flex-col sm:flex-row h-[calc(100vh-8rem)] bg-surface-menu-bar" @pointerdown.stop>
        <!-- Left sidebar: horizontal scrollable tabs on mobile, full-height rail from sm up -->
        <div class="flex shrink-0 flex-col overflow-y-auto border-b border-outline-gray-1 bg-surface-menu-bar sm:m-1 sm:w-48 sm:rounded-l-lg sm:border-b-0">
          <h3 class="hidden px-3 py-3 font-semibold text-ink-gray-9 sticky top-0 bg-surface-menu-bar sm:block">
            Settings
          </h3>
          <nav class="flex gap-1 overflow-x-auto p-1 sm:flex-col sm:gap-0 sm:space-y-0.5 sm:overflow-visible">
            <button
              v-for="tab in TABS"
              :key="tab.key"
              @click="activeTab = tab.key"
              class="flex h-7.5 shrink-0 cursor-pointer items-center rounded px-2 py-[7px] text-sm text-ink-gray-8 duration-300 ease-in-out focus:outline-none sm:w-full"
              :class="activeTab === tab.key
                ? 'bg-surface-selected shadow-sm'
                : 'hover:bg-surface-gray-2'"
            >
              {{ tab.label }}
            </button>
          </nav>
        </div>

        <!-- Right content area -->
        <div class="flex flex-col flex-1 overflow-hidden bg-surface-modal rounded-r-xl">
          <!-- Header with close button -->
          <div class="flex items-center justify-between px-6 py-4 border-b border-outline-gray-1 flex-shrink-0">
            <h3 class="font-semibold text-ink-gray-9">
              {{ TABS.find(t => t.key === activeTab)?.label }}
            </h3>
            <Button variant="ghost" @click="show = false">
              <template #icon>
                <LucideX class="h-4 w-4 text-ink-gray-7" />
              </template>
            </Button>
          </div>

          <!-- Scrollable content -->
          <div class="flex-1 overflow-y-auto px-6 py-5">
            <LoadingText v-if="loading" />
            <ErrorMessage v-else-if="loadError" :message="loadError" />

            <template v-else-if="form">
              <!-- Apps -->
              <div v-if="activeTab === 'apps'" class="flex flex-col gap-3">
                <TextInput v-model="appSearch" placeholder="Search apps…" />
                <LoadingText v-if="appsLoading" />
                <ErrorMessage v-else-if="appsError" :message="appsError" />
                <p v-else-if="!benchApps.length" class="py-8 text-center text-sm text-ink-gray-4">No apps installed on this bench.</p>
                <div v-else-if="!filteredApps.length" class="py-8 text-center text-sm text-ink-gray-4">No apps match your search.</div>
                <div v-else class="flex flex-col gap-2">
                  <div
                    v-for="app in filteredApps"
                    :key="app.name"
                    class="flex items-center gap-3 rounded-lg border border-outline-gray-1 px-4 py-3"
                  >
                    <div
                      class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md overflow-hidden"
                      :style="appLogoMap[app.name] ? {} : { background: hashColor(app.name) }"
                    >
                      <img v-if="appLogoMap[app.name]" :src="appLogoMap[app.name]" :alt="app.name" class="h-full w-full object-contain" />
                      <span v-else class="text-xs font-bold text-white leading-none">{{ app.name[0].toUpperCase() }}</span>
                    </div>
                    <div class="flex-1 min-w-0">
                      <div class="flex items-center gap-2 flex-wrap">
                        <span class="text-sm font-medium text-ink-gray-9">{{ appTitleMap[app.name] || app.name }}</span>
                        <Badge v-if="app.branch" :label="app.branch" theme="gray" size="sm" />
                        <Badge v-if="app.has_local_changes" label="Modified" theme="orange" size="sm" />
                      </div>
                      <p class="text-xs text-ink-gray-4 font-mono mt-0.5">{{ app.name }}</p>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Bench -->
              <div v-else-if="activeTab === 'bench'" class="flex flex-col gap-4">
                <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormControl label="Name" :modelValue="form.bench.name" disabled />
                  <FormControl label="Python Version" :modelValue="form.bench.python" disabled />
                  <FormControl label="Database" :modelValue="dbEngineLabel" disabled />
                  <FormControl type="number" label="HTTP Port" v-model="form.bench.http_port" />
                  <FormControl type="number" label="SocketIO Port" v-model="form.bench.socketio_port" />
                </div>
              </div>

              <!-- Appearance -->
              <div v-else-if="activeTab === 'appearance'" class="flex flex-col gap-4">
                <Select label="Theme" :options="THEME_OPTIONS" v-model="theme" class="w-full sm:w-48" />
              </div>

              <!-- MariaDB -->
              <div v-else-if="activeTab === 'mariadb'" class="flex flex-col gap-4">
                <p class="rounded-md bg-surface-gray-2 px-3 py-2 text-xs text-ink-gray-5">
                  MariaDB connection settings are set during bench initialization and cannot be changed here.
                </p>
                <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormControl label="Host" :modelValue="form.mariadb.host" disabled />
                  <FormControl type="number" label="Port" :modelValue="form.mariadb.port" disabled />
                  <FormControl label="Admin User" :modelValue="form.mariadb.admin_user" disabled />
                  <FormControl label="Version" :modelValue="form.mariadb.version" disabled />
                  <FormControl class="sm:col-span-2" label="Socket Path" :modelValue="form.mariadb.socket_path" disabled />
                </div>
              </div>

              <!-- PostgreSQL -->
              <div v-else-if="activeTab === 'postgres'" class="flex flex-col gap-4">
                <p class="rounded-md bg-surface-gray-2 px-3 py-2 text-xs text-ink-gray-5">
                  PostgreSQL connection settings are set during bench initialization and cannot be changed here.
                </p>
                <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormControl label="Host" :modelValue="form.postgres.host" disabled />
                  <FormControl type="number" label="Port" :modelValue="form.postgres.port" disabled />
                  <FormControl label="Admin User" :modelValue="form.postgres.admin_user" disabled />
                </div>
              </div>

              <!-- Redis -->
              <div v-else-if="activeTab === 'redis'" class="flex flex-col gap-4">
                <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormControl type="number" label="Cache Port" v-model="form.redis.cache_port" />
                  <FormControl type="number" label="Queue Port" v-model="form.redis.queue_port" />
                  <FormControl label="Version" v-model="form.redis.version" disabled placeholder="not installed" />
                </div>
              </div>

              <!-- Workers -->
              <div v-else-if="activeTab === 'workers'" class="flex flex-col gap-4">
                <p class="text-sm text-ink-gray-6">
                  Each group spawns <span class="font-medium">count</span> workers listening to the listed queues.
                </p>
                <div
                  v-for="(group, i) in form.workers"
                  :key="i"
                  class="grid grid-cols-[1fr_7rem_auto] items-end gap-3"
                >
                  <FormControl
                    :label="i === 0 ? 'Queues' : undefined"
                    v-model="group.queues"
                    placeholder="default, short, long"
                  />
                  <FormControl type="number" :min="1" :label="i === 0 ? 'Count' : undefined" v-model.number="group.count" />
                  <Button
                    variant="ghost"
                    icon="trash-2"
                    :disabled="form.workers.length === 1"
                    @click="removeWorkerGroup(i)"
                  />
                </div>
                <div>
                  <Button variant="subtle" icon-left="plus" label="Add group" @click="addWorkerGroup" />
                </div>
              </div>

              <!-- Monitoring -->
              <div v-else-if="activeTab === 'monitoring'" class="flex flex-col gap-4">
                <template v-if="form.production?.process_manager === 'none'">
                  <div class="flex flex-col gap-2 rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-4 py-5">
                    <p class="text-sm font-medium text-ink-gray-7">Monitoring is only active in production</p>
                    <p class="text-xs text-ink-gray-5">Deploy this bench to production to enable system and application monitoring.</p>
                    <div class="mt-2">
                      <p class="text-xs font-medium text-ink-gray-6">Deploy to production</p>
                      <code class="mt-1 block rounded bg-surface-gray-2 px-2 py-1.5 font-mono text-sm text-ink-gray-8 select-all">bench setup production --admin-domain &lt;your-domain&gt; --tls --letsencrypt-email &lt;you@example.com&gt;</code>
                    </div>
                  </div>
                </template>
                <template v-else>
                  <p class="rounded-md bg-surface-gray-2 px-3 py-2 text-xs text-ink-gray-5">
                    Log paths are set automatically during <span class="font-mono">bench setup production</span>.
                    Override them here if needed. Max sizes accept values like <span class="font-mono">500M</span> or <span class="font-mono">1G</span>.
                  </p>
                  <div class="flex flex-col gap-3">
                    <p class="text-sm font-medium text-ink-gray-7">System Log</p>
                    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <FormControl
                        class="sm:col-span-2"
                        label="Path"
                        v-model="form.monitor.system_log_path"
                        placeholder="/var/log/bench-system-stats.log"
                      />
                      <FormControl
                        label="Max Size"
                        v-model="form.monitor.system_log_max_size"
                        placeholder="500M"
                      />
                    </div>
                  </div>
                  <div class="flex flex-col gap-3">
                    <p class="text-sm font-medium text-ink-gray-7">Application Log</p>
                    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <FormControl
                        class="sm:col-span-2"
                        label="Path"
                        v-model="form.monitor.log_path"
                        :placeholder="`/var/log/${form.bench.name}-stats.log (default)`"
                      />
                      <FormControl
                        label="Max Size"
                        v-model="form.monitor.application_log_max_size"
                        placeholder="500M"
                      />
                    </div>
                  </div>
                  <!-- Monitor log file status -->
                  <LoadingText v-if="monitorStatusLoading" />
                  <div v-else-if="monitorStatus?.length" class="overflow-hidden rounded-lg border border-outline-gray-1">
                    <div class="grid border-b border-outline-gray-1 bg-surface-gray-1 px-3 py-2 text-xs font-medium text-ink-gray-5" style="grid-template-columns: 120px 1fr 110px">
                      <span>Description</span>
                      <span>Path</span>
                      <span>Last Modified</span>
                    </div>
                    <div
                      v-for="row in monitorStatus"
                      :key="row.description"
                      class="grid items-center border-b border-outline-gray-1 px-3 py-2.5 last:border-0"
                      style="grid-template-columns: 120px 1fr 110px"
                    >
                      <span class="text-sm text-ink-gray-7">{{ row.description }}</span>
                      <span class="min-w-0 truncate pr-3 font-mono text-xs text-ink-gray-5">{{ row.path }}</span>
                      <span>
                        <Badge v-if="row.last_modified" :label="formatRelativeTime(row.last_modified)" :theme="isRecentlyModified(row.last_modified) ? 'green' : 'orange'" />
                        <Badge v-else label="No data" theme="gray" />
                      </span>
                    </div>
                  </div>
                </template>
              </div>

              <!-- ZFS Volume -->
              <div v-else-if="activeTab === 'volume'" class="flex flex-col gap-4">
                <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormControl label="Pool Name" :modelValue="form.volume.pool" disabled />
                  <FormControl
                    v-if="form.volume.backing === 'image'"
                    label="Disk Image"
                    :modelValue="`${form.volume.image_path} (${form.volume.image_size})`"
                    disabled
                  />
                  <FormControl v-else label="Block Device" :modelValue="form.volume.device" disabled />
                </div>
                <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormControl label="Reservation" v-model="form.volume.reservation" />
                  <FormControl label="Quota" v-model="form.volume.quota" />
                </div>
              </div>

              <!-- Updates -->
              <div v-else-if="activeTab === 'updates'" class="flex flex-col gap-4">
                <Button variant="outline" :loading="checkingUpdate" @click="checkCliUpdate">Check for Updates</Button>
                <ErrorMessage :message="checkUpdateError" />
                <template v-if="cliUpdate">
                  <div class="flex items-center gap-3">
                    <Badge v-if="cliUpdate.update_available" label="Update available" theme="orange" />
                    <Badge v-else label="Up to date" theme="green" />
                    <Button v-if="cliUpdate.update_available" variant="solid" @click="updateCli">Update</Button>
                    <button class="ml-auto text-xs text-ink-gray-4 hover:text-ink-gray-7" @click="showUpdateDetails = !showUpdateDetails">
                      {{ showUpdateDetails ? 'Hide details' : 'Details' }}
                    </button>
                  </div>
                  <div v-if="showUpdateDetails" class="rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-4 py-3 flex flex-col gap-2.5 text-sm">
                    <div class="flex items-baseline gap-3">
                      <span class="w-14 shrink-0 text-ink-gray-5">Current</span>
                      <span class="font-mono text-xs font-semibold text-ink-gray-8 break-all">{{ cliUpdate.local_commit }}</span>
                    </div>
                    <div class="flex items-baseline gap-3">
                      <span class="w-14 shrink-0 text-ink-gray-5">Latest</span>
                      <span class="font-mono text-xs font-semibold text-ink-gray-8 break-all">{{ cliUpdate.remote_commit || '—' }}</span>
                    </div>
                  </div>
                </template>
              </div>

            </template>
          </div>

          <!-- Footer -->
          <div v-if="activeTab !== 'appearance' && activeTab !== 'updates' && activeTab !== 'apps' && !(activeTab === 'monitoring' && form?.production?.process_manager === 'none')" class="flex items-center justify-end gap-3 px-6 py-3 border-t border-outline-gray-1 flex-shrink-0">
            <ErrorMessage :message="saveError" />
            <span v-if="saveSuccess" class="text-sm text-ink-green-2 font-medium">{{ saveSuccess }}</span>
            <Button @click="show = false">Cancel</Button>
            <Button variant="solid" :loading="saving" @click="save">Save</Button>
          </div>
        </div>
      </div>
    </template>
  </Dialog>
</template>
