<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, Dialog, Dropdown, FormControl, ListView, LoadingText, ErrorMessage, Tabs } from 'frappe-ui'
import LucideDatabase from '~icons/lucide/database'
import LucideServer from '~icons/lucide/server'
import LucideMoreVertical from '~icons/lucide/more-vertical'
import LucideDownload from '~icons/lucide/download'
import LucideTrash2 from '~icons/lucide/trash-2'
import LucideTriangleAlert from '~icons/lucide/triangle-alert'
import LucideRefreshCw from '~icons/lucide/refresh-cw'
import LucidePlus from '~icons/lucide/plus'

const route = useRoute()
const router = useRouter()
const siteName = route.params.name

const site = ref(null)
const httpPort = ref(8000)
const nginxEnabled = ref(false)
const installable = ref([])
const registry = ref([])
const loading = ref(true)
const error = ref('')

const logoMap = computed(() => Object.fromEntries(registry.value.map(a => [a.name, a.logo_url])))
const titleMap = computed(() => Object.fromEntries(registry.value.map(a => [a.name, a.title])))

const actionLoading = ref('')
const actionError = ref('')

const showInstall = ref(false)
const selectedInstallApp = ref('')
const installLoading = ref(false)
const installError = ref('')

const showDrop = ref(false)
const dropConfirmText = ref('')
const showForceDrop = ref(false)
const forceDropLoading = ref(false)
const forceDropError = ref('')
const showUninstall = ref(false)
const uninstallTarget = ref('')
const forceUninstallLoading = ref(false)
const forceUninstallError = ref('')

const showLogin = ref(false)
const loginPassword = ref('')
const loginLoading = ref(false)
const loginError = ref('')

const sslLoading = ref(false)
const sslError = ref('')
const showSslEmail = ref(false)
const sslEmail = ref('')
const sslEmailError = ref('')

async function enableSsl(email) {
  sslError.value = ''
  sslEmailError.value = ''
  sslLoading.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/enable-ssl`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(email ? { email } : {}),
    })
    const d = await res.json()
    if (d.ok) {
      showSslEmail.value = false
      router.push(`/tasks/${d.task_id}`)
    } else if (d.needs_email) {
      // No Let's Encrypt email on file yet — prompt for one, then retry.
      showSslEmail.value = true
      if (email) sslEmailError.value = d.error
    } else {
      sslError.value = d.error
    }
  } catch (e) {
    if (showSslEmail.value) sslEmailError.value = e.message
    else sslError.value = e.message
  } finally {
    sslLoading.value = false
  }
}

async function loginToSite() {
  loginError.value = ''
  loginLoading.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: loginPassword.value }),
    })
    const d = await res.json()
    if (d.ok) {
      showLogin.value = false
      loginPassword.value = ''
      window.open(d.url, '_blank')
    } else {
      loginError.value = d.error
    }
  } catch (e) {
    loginError.value = e.message
  } finally {
    loginLoading.value = false
  }
}

// ── Config tab ─────────────────────────────────────────────────────────────
const showConfigEntry = ref(false)
const configEntryKey = ref('')
const configEntryValue = ref('')
const configEntryIsNew = ref(true)
const configEntryError = ref('')
const configSaving = ref(false)

const showDeleteConfig = ref(false)
const deleteConfigKey = ref('')
const deletingConfig = ref(false)
const deleteConfigError = ref('')

const configColumns = [
  { label: 'Key', key: 'key', align: 'left', width: 2 },
  { label: 'Value', key: 'value', align: 'left', width: 3 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const configRows = computed(() =>
  Object.entries(site.value?.site_config || {}).map(([key, value]) => ({
    name: key,
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }))
)

function configMenuOptions(key) {
  return [
    { label: 'Edit', onClick: () => openConfigEntry(key) },
    {
      label: 'Delete',
      icon: LucideTrash2,
      theme: 'red',
      onClick: () => { deleteConfigKey.value = key; deleteConfigError.value = ''; showDeleteConfig.value = true },
    },
  ]
}

// Parse a value as JSON (numbers, booleans, arrays, objects); fall back to raw text.
function parseConfigValue(raw) {
  const t = raw.trim()
  if (t === '') return ''
  try { return JSON.parse(t) } catch { return raw }
}

function openConfigEntry(key = null) {
  configEntryError.value = ''
  configEntryIsNew.value = key === null
  configEntryKey.value = key || ''
  if (key !== null) {
    const v = site.value.site_config[key]
    configEntryValue.value = typeof v === 'string' ? v : JSON.stringify(v)
  } else {
    configEntryValue.value = ''
  }
  showConfigEntry.value = true
}

async function patchConfig(next) {
  const res = await fetch(`/api/sites/${siteName}/config`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(next),
  })
  const d = await res.json()
  if (!d.ok) throw new Error(d.error || 'Failed to update config.')
}

async function saveConfigEntry() {
  const key = configEntryKey.value.trim()
  if (!key) { configEntryError.value = 'Key is required.'; return }
  if (configEntryIsNew.value && key in (site.value.site_config || {})) {
    configEntryError.value = 'That key already exists.'
    return
  }
  configEntryError.value = ''
  configSaving.value = true
  try {
    await patchConfig({ ...site.value.site_config, [key]: parseConfigValue(configEntryValue.value) })
    showConfigEntry.value = false
    await load()
  } catch (e) {
    configEntryError.value = e.message
  } finally {
    configSaving.value = false
  }
}

async function deleteConfigEntry() {
  deletingConfig.value = true
  deleteConfigError.value = ''
  try {
    const next = { ...site.value.site_config }
    delete next[deleteConfigKey.value]
    await patchConfig(next)
    showDeleteConfig.value = false
    await load()
  } catch (e) {
    deleteConfigError.value = e.message
  } finally {
    deletingConfig.value = false
  }
}

const activeTab = ref(0)
const tabs = [
  { label: 'Apps' },
  { label: 'Config' },
  { label: 'Backups' },
  { label: 'Actions' },
]

// ── Backups tab ──────────────────────────────────────────────────────────────
const backups = ref([])
const backupsLoading = ref(false)
const backupsError = ref('')
const backupsTabLoaded = ref(false)

const currentSchedule = ref(null)
const scheduleLoading = ref(false)
const scheduleSaving = ref(false)
const scheduleRemoving = ref(false)
const scheduleError = ref('')
const showSchedule = ref(false)

function openSchedule() {
  scheduleError.value = ''
  if (currentSchedule.value) {
    parseCronToState(currentSchedule.value)
  } else {
    schedFrequency.value = 'daily'
    schedHour.value = 2
    schedWeekday.value = 0
    schedMonthDay.value = 1
  }
  showSchedule.value = true
}

const schedFrequency = ref('daily')
const schedHour = ref(2)
const schedWeekday = ref(0)
const schedMonthDay = ref(1)

const WEEKDAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

const hourOptions = Array.from({ length: 24 }, (_, i) => ({
  label: i === 0 ? '12:00 AM' : i < 12 ? `${i}:00 AM` : i === 12 ? '12:00 PM' : `${i - 12}:00 PM`,
  value: i,
}))

function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}

const monthDayOptions = Array.from({ length: 28 }, (_, i) => ({
  label: ordinal(i + 1),
  value: i + 1,
}))

const schedCron = computed(() => {
  const h = Number(schedHour.value)
  if (schedFrequency.value === 'weekly') return `0 ${h} * * ${schedWeekday.value}`
  if (schedFrequency.value === 'monthly') return `0 ${h} ${Number(schedMonthDay.value)} * *`
  return `0 ${h} * * *`
})

// Human-readable label for the current picker state (independent of whether a schedule is active yet).
const scheduleLabel = computed(() => {
  const h = Number(schedHour.value)
  const ampm = h === 0 ? '12:00 AM' : h < 12 ? `${h}:00 AM` : h === 12 ? '12:00 PM' : `${h - 12}:00 PM`
  if (schedFrequency.value === 'weekly') return `Every ${WEEKDAY_LABELS[schedWeekday.value]} at ${ampm}`
  if (schedFrequency.value === 'monthly') return `Monthly on the ${ordinal(Number(schedMonthDay.value))} at ${ampm}`
  return `Daily at ${ampm}`
})

// Label shown on the status bar — only meaningful when a schedule is active.
const currentScheduleLabel = computed(() => (currentSchedule.value ? scheduleLabel.value : null))

function parseCronToState(expr) {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return
  const [, hour, dom, , dow] = parts
  const h = parseInt(hour)
  schedHour.value = isNaN(h) ? 0 : h
  if (dom !== '*' && dom !== '?') {
    schedFrequency.value = 'monthly'
    schedMonthDay.value = parseInt(dom) || 1
  } else if (dow !== '*' && dow !== '?') {
    schedFrequency.value = 'weekly'
    schedWeekday.value = parseInt(dow) || 0
  } else {
    schedFrequency.value = 'daily'
  }
}

const showDeleteBackup = ref(false)
const deleteBackupTarget = ref(null)
const deletingBackup = ref(false)
const deleteBackupError = ref('')

async function deleteBackupSet() {
  deletingBackup.value = true
  deleteBackupError.value = ''
  try {
    const filenames = deleteBackupTarget.value.files.map(f => f.filename)
    const res = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'delete-backup', site: siteName, filenames }),
    })
    const d = await res.json()
    if (d.ok) { showDeleteBackup.value = false; router.push(`/tasks/${d.task_id}`) }
    else deleteBackupError.value = d.error
  } catch (e) {
    deleteBackupError.value = e.message
  } finally {
    deletingBackup.value = false
  }
}

watch(activeTab, (idx) => {
  if (tabs[idx]?.label === 'Backups' && !backupsTabLoaded.value) {
    backupsTabLoaded.value = true
    loadBackups()
    loadSchedule()
  }
})

async function loadBackups() {
  backupsLoading.value = true
  backupsError.value = ''
  try {
    const res = await fetch(`/api/sites/${siteName}/backups`)
    const d = await res.json()
    if (d.error) backupsError.value = d.error
    else backups.value = d
  } catch (e) {
    backupsError.value = e.message
  } finally {
    backupsLoading.value = false
  }
}

async function loadSchedule() {
  scheduleLoading.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/backup-schedule`)
    const d = await res.json()
    currentSchedule.value = d.schedule ?? null
    if (d.schedule) parseCronToState(d.schedule)
  } finally {
    scheduleLoading.value = false
  }
}

async function saveSchedule() {
  scheduleError.value = ''
  scheduleSaving.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/backup-schedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ schedule: schedCron.value }),
    })
    const d = await res.json()
    if (d.ok) {
      await loadSchedule()
      showSchedule.value = false
    } else {
      scheduleError.value = d.error
    }
  } catch (e) {
    scheduleError.value = e.message
  } finally {
    scheduleSaving.value = false
  }
}

async function removeSchedule() {
  scheduleError.value = ''
  scheduleRemoving.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/backup-schedule`, { method: 'DELETE' })
    const d = await res.json()
    if (d.ok) {
      currentSchedule.value = null
      schedFrequency.value = 'daily'
      schedHour.value = 2
      schedWeekday.value = 0
      schedMonthDay.value = 1
      showSchedule.value = false
    } else {
      scheduleError.value = d.error
    }
  } catch (e) {
    scheduleError.value = e.message
  } finally {
    scheduleRemoving.value = false
  }
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatBackupDate(iso) {
  return new Date(iso).toLocaleString()
}

// Backup file kinds: 'public-file' | 'private-file' | 'database' | 'site_config'
function backupFile(set, kind) {
  return set.files.find(f => f.kind === kind) || null
}

function backupFileSize(set, kind) {
  const f = backupFile(set, kind)
  return f ? formatSize(f.size_bytes) : '—'
}

function downloadBackupFile(set, kind) {
  const f = backupFile(set, kind)
  if (!f) return
  window.location.href = `/api/sites/${siteName}/backups/download?filename=${encodeURIComponent(f.filename)}`
}

function backupMenuOptions(set) {
  const opts = []
  const downloads = [
    ['public-file', 'Download Public'],
    ['private-file', 'Download Private'],
    ['database', 'Download Database'],
    ['site_config', 'Download Config'],
  ]
  for (const [kind, label] of downloads) {
    if (backupFile(set, kind)) {
      opts.push({ label, icon: LucideDownload, onClick: () => downloadBackupFile(set, kind) })
    }
  }
  opts.push({
    label: 'Delete backup',
    icon: LucideTrash2,
    theme: 'red',
    onClick: () => { deleteBackupTarget.value = set; showDeleteBackup.value = true },
  })
  return opts
}

const backupColumns = [
  { label: 'Timestamp', key: 'timestamp', align: 'left', width: 2 },
  { label: 'Private', key: 'private', align: 'center', width: 1 },
  { label: 'Public', key: 'public', align: 'center', width: 1 },
  { label: 'Database', key: 'database', align: 'center', width: 1 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const backupRows = computed(() => backups.value.map(set => ({
  name: set.timestamp,
  timestamp: formatBackupDate(set.created_at),
  private: backupFileSize(set, 'private-file'),
  public: backupFileSize(set, 'public-file'),
  database: backupFileSize(set, 'database'),
  set,
})))

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']
function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

async function load() {
  try {
    const res = await fetch(`/api/sites/${siteName}`)
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    site.value = d.site
    httpPort.value = d.http_port ?? 8000
    nginxEnabled.value = d.nginx_enabled ?? false
    installable.value = d.installable_apps
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadRegistry() {
  try {
    const res = await fetch('/api/apps/registry')
    registry.value = await res.json()
  } catch { registry.value = [] }
}

async function doAction(path, body = {}) {
  actionError.value = ''
  actionLoading.value = path
  try {
    const res = await fetch(`/api/sites/${siteName}/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const d = await res.json()
    if (d.ok) router.push(`/tasks/${d.task_id}`)
    else actionError.value = d.error
  } catch (e) {
    actionError.value = e.message
  } finally {
    actionLoading.value = ''
  }
}

async function installApp() {
  if (!selectedInstallApp.value) return
  installLoading.value = true
  installError.value = ''
  try {
    const res = await fetch(`/api/sites/${siteName}/install-app`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app: selectedInstallApp.value }),
    })
    const d = await res.json()
    if (d.ok) { showInstall.value = false; router.push(`/tasks/${d.task_id}`) }
    else installError.value = d.error
  } catch (e) {
    installError.value = e.message
  } finally {
    installLoading.value = false
  }
}

function confirmUninstall(app) {
  uninstallTarget.value = app
  forceUninstallError.value = ''
  showUninstall.value = true
}

async function doForceUninstall() {
  forceUninstallError.value = ''
  forceUninstallLoading.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/force-uninstall-app`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app: uninstallTarget.value }),
    })
    const d = await res.json()
    if (d.ok) {
      showUninstall.value = false
      await load()
    } else {
      forceUninstallError.value = d.error
    }
  } catch (e) {
    forceUninstallError.value = e.message
  } finally {
    forceUninstallLoading.value = false
  }
}

async function forceDrop() {
  forceDropError.value = ''
  forceDropLoading.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/force-drop`, { method: 'POST' })
    const d = await res.json()
    if (d.ok) router.push('/sites')
    else forceDropError.value = d.error
  } catch (e) {
    forceDropError.value = e.message
  } finally {
    forceDropLoading.value = false
  }
}

onMounted(() => { load(); loadRegistry() })
</script>

<template>
  <div class="mx-auto flex max-w-2xl flex-col gap-6">
    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <template v-else-if="site">
      <!-- Site header -->
      <div class="flex items-start justify-between gap-4">
        <div class="flex flex-col gap-1.5">
          <div class="flex items-center gap-2">
            <h1 class="flex items-center gap-1.5 font-semibold text-ink-gray-9">
              {{ siteName }}
              <span class="group relative inline-flex h-2 w-2 shrink-0 rounded-full"
                :class="!site.exists ? 'bg-ink-gray-3' : site.broken ? 'bg-surface-red-4' : 'bg-surface-green-3'">
                <span
                  class="pointer-events-none absolute bottom-full left-1/2 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded bg-ink-gray-9 px-1.5 py-0.5 text-[10px] text-surface-white opacity-0 transition-opacity group-hover:opacity-100">
                  {{ !site.exists ? 'Offline' : site.broken ? 'Broken' : 'Online' }}
                </span>
              </span>
            </h1>
            <Badge v-if="site.ssl" label="SSL" theme="blue" />
          </div>
          <div class="flex items-center gap-4 text-sm text-ink-gray-5">
            <span v-if="site.db_name" class="flex items-center gap-1.5">
              <LucideDatabase class="h-3.5 w-3.5" />
              {{ site.db_name }}
            </span>
            <span v-if="site.db_host" class="flex items-center gap-1.5">
              <LucideServer class="h-3.5 w-3.5" />
              {{ site.db_host }}
            </span>
            <span class="flex items-center gap-1.5">
              :{{ httpPort }}
            </span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <Button variant="solid" @click="showLogin = true">
            Login to Site
          </Button>
        </div>
      </div>

      <ErrorMessage :message="actionError" />

      <!-- Tabs -->
      <Tabs :tabs="tabs" v-model="activeTab">
        <template #tab-panel="{ tab }">
          <!-- Apps -->
          <div v-if="tab.label === 'Apps'" class="pt-4">
            <div class="rounded-lg border border-outline-gray-2">
              <div class="flex items-center justify-between border-b border-outline-gray-2 px-4 py-2.5">
                <h3 class="text-sm font-semibold text-ink-gray-9">Installed Apps</h3>
                <Button v-if="installable.length" variant="ghost" size="sm" @click="showInstall = true">
                  <template #prefix><LucidePlus class="h-4 w-4" /></template>
                  Install App
                </Button>
              </div>
              <div v-if="!site.installed_apps.length" class="py-12 text-center text-sm text-ink-gray-4">
                No apps installed on this site.
              </div>
              <div v-else class="divide-y divide-outline-gray-1">
                <div v-for="app in site.installed_apps" :key="app" class="flex items-center justify-between px-4 py-3">
                  <div class="flex items-center gap-3">
                    <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md overflow-hidden"
                      :style="logoMap[app] ? {} : { background: hashColor(app) }">
                      <img v-if="logoMap[app]" :src="logoMap[app]" :alt="app" class="h-full w-full object-contain" />
                      <span v-else class="text-sm font-bold text-white">{{ app[0].toUpperCase() }}</span>
                    </div>
                    <span class="text-sm font-medium text-ink-gray-8">{{ titleMap[app] || app }}</span>
                  </div>
                  <Button v-if="app !== 'frappe'" variant="ghost" theme="red" size="sm" @click="confirmUninstall(app)">
                    Uninstall
                  </Button>
                </div>
              </div>
            </div>
          </div>

          <!-- Config -->
          <div v-else-if="tab.label === 'Config'" class="pt-4 flex flex-col gap-2">
            <div class="rounded-lg border border-outline-gray-2">
              <div class="flex items-center justify-between px-4 py-2.5">
                <h3 class="text-sm font-semibold text-ink-gray-9">Site Config</h3>
                <Button variant="ghost" size="sm" @click="openConfigEntry()">
                  <template #prefix>
                    <LucidePlus class="h-4 w-4" />
                  </template>
                  Add Key
                </Button>
              </div>
              <div v-if="!configRows.length" class="py-12 text-center text-sm text-ink-gray-4">
                No editable config keys.
              </div>
              <ListView v-else class="px-2 pb-2" :columns="configColumns" :rows="configRows" row-key="name"
                :options="{ selectable: false, showTooltip: false, rowHeight: 44 }">
                <template #cell="{ column, row, item }">
                  <div v-if="column.key === 'actions'" class="flex w-full justify-end">
                    <Dropdown :options="configMenuOptions(row.key)" placement="left">
                      <template #default="{ open }">
                        <Button variant="ghost" size="sm" :active="open">
                          <template #icon>
                            <LucideMoreVertical class="h-4 w-4" />
                          </template>
                        </Button>
                      </template>
                    </Dropdown>
                  </div>
                  <div v-else class="w-full truncate text-sm"
                    :class="column.key === 'key' ? 'font-medium text-ink-gray-8' : 'font-mono text-ink-gray-6'">{{ item
                    }}</div>
                </template>
              </ListView>
            </div>
            <p class="px-1 text-xs text-ink-gray-4">
              Database credentials, installed apps and SSL are managed by the system and aren't shown or editable here.
            </p>
          </div>

          <!-- Backups -->
          <div v-else-if="tab.label === 'Backups'" class="pt-4 flex flex-col gap-4">
            <!-- Automatic backup status — click to configure -->
            <button type="button" @click="openSchedule"
              class="group flex items-center gap-2.5 rounded-lg border border-outline-gray-2 px-3.5 py-2.5 text-left transition-colors hover:border-outline-gray-3 hover:bg-surface-gray-1">
              <span class="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                :class="currentSchedule ? 'bg-surface-green-3' : 'bg-ink-gray-4'"></span>
              <span class="text-sm font-medium text-ink-gray-8">Automatic backups</span>
              <span class="text-sm text-ink-gray-5">
                <template v-if="scheduleLoading">Loading…</template>
                <template v-else-if="currentSchedule">· {{ currentScheduleLabel }}</template>
                <template v-else>· Off</template>
              </span>
              <span class="ml-auto shrink-0 text-sm font-medium text-ink-gray-5 group-hover:text-ink-gray-8">
                {{ currentSchedule ? 'Configure' : 'Enable' }}
              </span>
            </button>

            <!-- Backups list -->
            <div class="rounded-lg border border-outline-gray-2">
              <div class="flex items-center justify-between px-4 py-2.5">
                <h3 class="text-sm font-semibold text-ink-gray-9">Backups</h3>
                <div class="flex items-center gap-1">
                  <Button variant="ghost" size="sm" :loading="actionLoading === 'backup'" @click="doAction('backup')">
                    Backup now
                  </Button>
                  <Button variant="ghost" size="sm" :loading="backupsLoading" @click="loadBackups" title="Refresh">
                    <template #icon>
                      <LucideRefreshCw class="h-4 w-4" />
                    </template>
                  </Button>
                </div>
              </div>
              <div v-if="backupsLoading" class="py-10 text-center text-sm text-ink-gray-5">Loading…</div>
              <div v-else-if="backupsError" class="p-4">
                <ErrorMessage :message="backupsError" />
              </div>
              <div v-else-if="!backups.length" class="py-12 text-center text-sm text-ink-gray-4">
                No backups yet.
              </div>
              <ListView v-else class="px-2 pb-2" :columns="backupColumns" :rows="backupRows" row-key="name"
                :options="{ selectable: false, showTooltip: false, rowHeight: 44 }">
                <template #cell="{ column, row, item, align }">
                  <div v-if="column.key === 'actions'" class="flex w-full justify-end">
                    <Dropdown :options="backupMenuOptions(row.set)" placement="left">
                      <template #default="{ open }">
                        <Button variant="ghost" size="sm" :active="open">
                          <template #icon>
                            <LucideMoreVertical class="h-4 w-4" />
                          </template>
                        </Button>
                      </template>
                    </Dropdown>
                  </div>
                  <div v-else class="w-full truncate text-sm"
                    :class="column.key === 'timestamp' ? 'text-left text-ink-gray-8' : 'text-center font-mono text-ink-gray-6'">
                    {{ item
                    }}</div>
                </template>
              </ListView>
            </div>
          </div>

          <!-- Actions -->
          <div v-else-if="tab.label === 'Actions'" class="pt-4 flex flex-col gap-5">
            <!-- Regular actions -->
            <div class="divide-y divide-outline-gray-1 rounded-lg border border-outline-gray-2">
              <!-- Backup -->
              <div class="flex items-center justify-between gap-4 px-4 py-3.5">
                <div>
                  <p class="text-sm font-medium text-ink-gray-8">Backup Site</p>
                  <p class="mt-0.5 text-sm text-ink-gray-5">Create an on-demand backup of the database and files.</p>
                </div>
                <Button variant="outline" class="shrink-0" :loading="actionLoading === 'backup'"
                  @click="doAction('backup')">
                  Backup
                </Button>
              </div>

              <!-- Enable SSL -->
              <div class="flex items-center justify-between gap-4 px-4 py-3.5">
                <div>
                  <p class="text-sm font-medium text-ink-gray-8">Enable SSL</p>
                  <p class="mt-0.5 text-sm text-ink-gray-5">
                    <template v-if="site.ssl">A Let's Encrypt certificate is already active for this site.</template>
                    <template v-else-if="!nginxEnabled">Available once this bench is live (deployed to production).</template>
                    <template v-else>Issue a Let's Encrypt certificate and serve this site over HTTPS.</template>
                  </p>
                </div>
                <Badge v-if="site.ssl" label="Enabled" theme="green" class="shrink-0" />
                <Button v-else variant="outline" class="shrink-0" :disabled="!nginxEnabled" :loading="sslLoading"
                  @click="enableSsl()">
                  Enable SSL
                </Button>
              </div>
            </div>
            <ErrorMessage :message="sslError" />

            <!-- Danger zone -->
            <div class="overflow-hidden rounded-lg border border-outline-red-2">
              <div class="border-b border-outline-red-2 bg-surface-red-1 px-4 py-2.5">
                <p class="text-sm font-semibold text-ink-red-4">Danger Zone</p>
              </div>
              <div class="divide-y divide-outline-red-1">
                <!-- Drop Site -->
                <div class="flex items-center justify-between gap-4 px-4 py-3.5">
                  <div>
                    <p class="text-sm font-medium text-ink-gray-8">Drop Site</p>
                    <p class="mt-0.5 text-sm text-ink-gray-5">
                      Permanently delete <strong>{{ siteName }}</strong> and all its data. This cannot be undone.
                    </p>
                  </div>
                  <Button variant="subtle" theme="red" class="shrink-0" @click="showDrop = true">
                    Drop Site
                  </Button>
                </div>

                <!-- Force Delete -->
                <div v-if="site.broken" class="flex items-center justify-between gap-4 px-4 py-3.5">
                  <div>
                    <p class="text-sm font-medium text-ink-gray-8">Force Delete</p>
                    <p class="mt-0.5 text-sm text-ink-gray-5">
                      This site is broken (database unreachable). Remove the site directory without running frappe
                      cleanup.
                    </p>
                  </div>
                  <Button variant="solid" theme="red" class="shrink-0" @click="showForceDrop = true">
                    Force Delete
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </template>
      </Tabs>
    </template>

    <!-- Install App dialog -->
    <Dialog v-model="showInstall" :options="{ title: 'Install App' }">
      <template #body-content>
        <div @pointerdown.stop>
          <FormControl label="App to install" type="select" v-model="selectedInstallApp"
            :options="[{ label: 'Select an app…', value: '' }, ...installable.map(a => ({ label: a, value: a }))]" />
          <ErrorMessage :message="installError" class="mt-2" />
          <div class="mt-4 flex justify-end gap-2">
            <Button variant="ghost" @click="showInstall = false">Cancel</Button>
            <Button variant="solid" :loading="installLoading" :disabled="!selectedInstallApp"
              @click="installApp">Install</Button>
          </div>
        </div>
      </template>
    </Dialog>

    <!-- Drop Site dialog -->
    <Dialog v-model="showDrop" :options="{ title: 'Drop Site', size: 'sm' }" @close="dropConfirmText = ''">
      <template #body-content>
        <div @pointerdown.stop>
          <div class="flex items-start gap-3 rounded-md border border-outline-red-2 bg-surface-red-1 p-3">
            <LucideTriangleAlert class="mt-0.5 h-4 w-4 shrink-0 text-ink-red-4" />
            <p class="text-sm leading-relaxed text-ink-gray-7">
              This permanently deletes <strong>{{ siteName }}</strong>, including its database and all files.
              This action <strong>cannot be undone</strong>.
            </p>
          </div>
          <FormControl class="mt-4" type="text" :label="`Type the site name to confirm`" v-model="dropConfirmText"
            :placeholder="siteName" autocomplete="off"
            @keydown.enter="dropConfirmText === siteName && (showDrop = false, doAction('drop'))" />
          <div class="mt-4 flex justify-end gap-2">
            <Button variant="ghost" @click="showDrop = false">Cancel</Button>
            <Button variant="solid" theme="red" :loading="actionLoading === 'drop'"
              :disabled="dropConfirmText !== siteName" @click="showDrop = false; doAction('drop')">Drop Site</Button>
          </div>
        </div>
      </template>
    </Dialog>

    <!-- Force Drop dialog -->
    <Dialog v-model="showForceDrop" :options="{ title: 'Force Delete Site', size: 'sm' }">
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          Force delete <strong>{{ siteName }}</strong>? The site directory will be removed immediately without frappe
          cleanup. The database will <strong>not</strong> be dropped.
        </p>
        <ErrorMessage v-if="forceDropError" :message="forceDropError" class="mt-2" />
        <div class="mt-4 flex justify-end gap-2">
          <Button variant="ghost" @click="showForceDrop = false">Cancel</Button>
          <Button variant="solid" theme="red" :loading="forceDropLoading"
            @click="showForceDrop = false; forceDrop()">Force Delete</Button>
        </div>
      </template>
    </Dialog>

    <!-- Login dialog -->
    <Dialog v-model="showSslEmail" :options="{ title: 'Enable SSL', size: 'sm' }">
      <template #body-content>
        <div @pointerdown.stop>
          <p class="text-sm text-ink-gray-6">
            A Let's Encrypt account email is required to issue certificates. It's saved to this
            bench's config and reused for future certificates and renewal notices.
          </p>
          <FormControl class="mt-3" label="Let's Encrypt email" type="email" v-model="sslEmail"
            placeholder="you@example.com" @keydown.enter="enableSsl(sslEmail)" />
          <ErrorMessage :message="sslEmailError" class="mt-2" />
          <div class="mt-4 flex justify-end gap-2">
            <Button variant="ghost" @click="showSslEmail = false">Cancel</Button>
            <Button variant="solid" :loading="sslLoading" :disabled="!sslEmail" @click="enableSsl(sslEmail)">
              Enable SSL
            </Button>
          </div>
        </div>
      </template>
    </Dialog>

    <Dialog v-model="showLogin" :options="{ title: 'Login to Site', size: 'sm' }">
      <template #body-content>
        <div @pointerdown.stop>
          <FormControl label="Administrator password" type="password" v-model="loginPassword" placeholder="admin"
            @keydown.enter="loginToSite" />
          <ErrorMessage :message="loginError" class="mt-2" />
          <div class="mt-4 flex justify-end gap-2">
            <Button variant="ghost" @click="showLogin = false">Cancel</Button>
            <Button variant="solid" :loading="loginLoading" :disabled="!loginPassword" @click="loginToSite">
              Login
            </Button>
          </div>
        </div>
      </template>
    </Dialog>

    <!-- Add / Edit config key dialog -->
    <Dialog v-model="showConfigEntry"
      :options="{ title: configEntryIsNew ? 'Add Config Key' : 'Edit Config Key', size: 'sm' }">
      <template #body-content>
        <div @pointerdown.stop class="flex flex-col gap-3">
          <FormControl label="Key" type="text" v-model="configEntryKey" :disabled="!configEntryIsNew"
            placeholder="key_name" />
          <FormControl label="Value" type="textarea" v-model="configEntryValue" :rows="3"
            placeholder='"text", 42, true, ["a", "b"]' />
          <p class="text-xs text-ink-gray-4">
            Parsed as JSON when possible (numbers, booleans, arrays, objects); otherwise stored as text.
          </p>
          <ErrorMessage :message="configEntryError" />
          <div class="mt-1 flex justify-end gap-2">
            <Button variant="ghost" @click="showConfigEntry = false">Cancel</Button>
            <Button variant="solid" :loading="configSaving" @click="saveConfigEntry">Save</Button>
          </div>
        </div>
      </template>
    </Dialog>

    <!-- Delete config key dialog -->
    <Dialog v-model="showDeleteConfig" :options="{ title: 'Delete Config Key', size: 'sm' }">
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          Remove <strong>{{ deleteConfigKey }}</strong> from <strong>site_config.json</strong>?
        </p>
        <ErrorMessage v-if="deleteConfigError" :message="deleteConfigError" class="mt-2" />
        <div class="mt-4 flex justify-end gap-2">
          <Button variant="ghost" @click="showDeleteConfig = false">Cancel</Button>
          <Button variant="solid" theme="red" :loading="deletingConfig" @click="deleteConfigEntry">Delete</Button>
        </div>
      </template>
    </Dialog>

    <!-- Delete Backup dialog -->
    <Dialog v-model="showDeleteBackup" :options="{ title: 'Delete Backup', size: 'sm' }">
      <template #body-content>
        <p class="text-sm leading-relaxed text-ink-gray-7">
          Delete the backup from <strong>{{ deleteBackupTarget ? formatBackupDate(deleteBackupTarget.created_at) : ''
          }}</strong>? This cannot be undone.
        </p>
        <ErrorMessage v-if="deleteBackupError" :message="deleteBackupError" class="mt-2" />
        <div class="mt-4 flex justify-end gap-2">
          <Button variant="ghost" @click="showDeleteBackup = false">Cancel</Button>
          <Button variant="solid" theme="red" :loading="deletingBackup" @click="deleteBackupSet">Delete</Button>
        </div>
      </template>
    </Dialog>

    <!-- Backup Schedule dialog -->
    <Dialog v-model="showSchedule" :options="{ title: 'Automatic Backups', size: 'sm' }">
      <template #body-content>
        <div @pointerdown.stop class="flex flex-col gap-4">
          <!-- Frequency picker -->
          <div class="flex flex-col gap-1.5">
            <span class="text-xs font-medium text-ink-gray-6">Frequency</span>
            <div class="flex gap-1.5">
              <button v-for="f in ['daily', 'weekly', 'monthly']" :key="f"
                class="rounded-md border px-3 py-1.5 text-sm capitalize transition-colors"
                :class="schedFrequency === f
                  ? 'border-outline-gray-4 bg-surface-gray-3 text-ink-gray-9'
                  : 'border-outline-gray-2 bg-surface-white text-ink-gray-6 hover:border-outline-gray-3 hover:text-ink-gray-8'" @click="schedFrequency = f">{{ f }}</button>
            </div>
          </div>

          <!-- Weekday picker (weekly only) -->
          <div v-if="schedFrequency === 'weekly'" class="flex flex-col gap-1.5">
            <span class="text-xs font-medium text-ink-gray-6">Day of week</span>
            <div class="flex gap-1">
              <button v-for="(day, idx) in WEEKDAY_LABELS" :key="idx"
                class="w-10 rounded-md border py-1.5 text-xs font-medium transition-colors"
                :class="schedWeekday === idx
                  ? 'border-outline-gray-4 bg-surface-gray-3 text-ink-gray-9'
                  : 'border-outline-gray-2 bg-surface-white text-ink-gray-6 hover:border-outline-gray-3 hover:text-ink-gray-8'" @click="schedWeekday = idx">{{ day }}</button>
            </div>
          </div>

          <!-- Month day picker (monthly only) -->
          <div v-if="schedFrequency === 'monthly'" class="flex flex-col gap-1.5">
            <span class="text-xs font-medium text-ink-gray-6">Day of month</span>
            <FormControl type="select" v-model="schedMonthDay" :options="monthDayOptions" class="w-40" />
          </div>

          <!-- Time picker -->
          <div class="flex flex-col gap-1.5">
            <span class="text-xs font-medium text-ink-gray-6">Time</span>
            <FormControl type="select" v-model="schedHour" :options="hourOptions" class="w-40" />
          </div>

          <p class="text-sm text-ink-gray-5">
            Backups will run <strong class="text-ink-gray-7">{{ scheduleLabel.toLowerCase() }}</strong>.
          </p>

          <ErrorMessage :message="scheduleError" />

          <div class="flex items-center justify-between gap-2 pt-1">
            <Button v-if="currentSchedule" variant="subtle" theme="red" :loading="scheduleRemoving"
              @click="removeSchedule">
              Disable
            </Button>
            <span v-else></span>
            <div class="flex items-center gap-2">
              <Button variant="ghost" @click="showSchedule = false">Cancel</Button>
              <Button variant="solid" :loading="scheduleSaving" @click="saveSchedule">
                {{ currentSchedule ? 'Save' : 'Enable' }}
              </Button>
            </div>
          </div>
        </div>
      </template>
    </Dialog>

    <!-- Uninstall App dialog -->
    <Dialog v-model="showUninstall" :options="{ title: 'Uninstall App', size: 'sm' }">
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          Uninstall <strong>{{ uninstallTarget }}</strong> from <strong>{{ siteName }}</strong>?
        </p>
        <div class="mt-4 flex justify-end gap-2">
          <Button variant="ghost" @click="showUninstall = false">Cancel</Button>
          <Button variant="solid" theme="red"
            @click="showUninstall = false; doAction('uninstall-app', { app: uninstallTarget })">Uninstall</Button>
        </div>
        <div class="mt-4 border-t border-outline-gray-1 pt-3">
          <p class="mb-2 text-xs text-ink-gray-4">If the app is broken and normal uninstall fails, force-remove it from
            the site's app list without running any app scripts.</p>
          <ErrorMessage :message="forceUninstallError" class="mb-2" />
          <Button variant="outline" theme="red" size="sm" :loading="forceUninstallLoading" @click="doForceUninstall">
            Force Remove
          </Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
