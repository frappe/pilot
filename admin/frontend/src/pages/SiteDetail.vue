<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, Dialog, FormControl, LoadingText, ErrorMessage, Tabs } from 'frappe-ui'
import LucideDatabase from '~icons/lucide/database'
import LucideServer from '~icons/lucide/server'
import ConfigTree from '../components/ConfigTree.vue'

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

const actionLoading = ref('')
const actionError = ref('')

const showInstall = ref(false)
const selectedInstallApp = ref('')
const installLoading = ref(false)
const installError = ref('')

const showDrop = ref(false)
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

async function enableSsl() {
  sslError.value = ''
  sslLoading.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/enable-ssl`, { method: 'POST' })
    const d = await res.json()
    if (d.ok) router.push(`/tasks/${d.task_id}`)
    else sslError.value = d.error
  } catch (e) {
    sslError.value = e.message
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

const showEditConfig = ref(false)
const editConfigText = ref('')
const editConfigError = ref('')
const editConfigLoading = ref(false)

function openEditConfig() {
  editConfigText.value = JSON.stringify(site.value.site_config, null, 2)
  editConfigError.value = ''
  showEditConfig.value = true
}

async function saveConfig() {
  editConfigError.value = ''
  let parsed
  try {
    parsed = JSON.parse(editConfigText.value)
  } catch {
    editConfigError.value = 'Invalid JSON — please fix the syntax before saving.'
    return
  }
  editConfigLoading.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/config`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(parsed),
    })
    const d = await res.json()
    if (d.ok) {
      showEditConfig.value = false
      await load()
    } else {
      editConfigError.value = d.error
    }
  } catch (e) {
    editConfigError.value = e.message
  } finally {
    editConfigLoading.value = false
  }
}

const activeTab = ref(0)
const tabs = [
  { label: 'Apps' },
  { label: 'Config' },
  { label: 'Backups' },
  { label: 'Danger Zone' },
]

// ── Backups tab ──────────────────────────────────────────────────────────────
const backups = ref([])
const backupsLoading = ref(false)
const backupsError = ref('')
const backupsTabLoaded = ref(false)

const currentSchedule = ref(null)
const scheduleInput = ref('')
const scheduleLoading = ref(false)
const scheduleSaving = ref(false)
const scheduleRemoving = ref(false)
const scheduleError = ref('')

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

const schedulePresets = [
  { label: 'Daily midnight', value: '0 0 * * *' },
  { label: 'Daily 2am', value: '0 2 * * *' },
  { label: 'Weekly (Sun 2am)', value: '0 2 * * 0' },
  { label: 'Monthly (1st 2am)', value: '0 2 1 * *' },
]

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
    scheduleInput.value = d.schedule ?? ''
  } finally {
    scheduleLoading.value = false
  }
}

const CRON_RE = /^(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)$/

async function saveSchedule() {
  scheduleError.value = ''
  if (!scheduleInput.value.trim()) {
    scheduleError.value = 'Schedule expression is required.'
    return
  }
  if (!CRON_RE.test(scheduleInput.value.trim())) {
    scheduleError.value = "Invalid cron expression. Expected 5 fields like '0 2 * * *' (minute hour day month weekday)."
    return
  }
  scheduleSaving.value = true
  try {
    const res = await fetch(`/api/sites/${siteName}/backup-schedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ schedule: scheduleInput.value }),
    })
    const d = await res.json()
    if (d.ok) await loadSchedule()
    else scheduleError.value = d.error
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
    if (d.ok) { currentSchedule.value = null; scheduleInput.value = '' }
    else scheduleError.value = d.error
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
            <h1 class="flex items-center gap-1.5 text-2xl font-semibold text-ink-gray-9">
              {{ siteName }}
              <span
                class="group relative inline-flex h-2 w-2 shrink-0 rounded-full"
                :class="!site.exists ? 'bg-ink-gray-3' : site.broken ? 'bg-surface-red-4' : 'bg-surface-green-3'"
              >
                <span class="pointer-events-none absolute bottom-full left-1/2 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded bg-ink-gray-9 px-1.5 py-0.5 text-[10px] text-surface-white opacity-0 transition-opacity group-hover:opacity-100">
                  {{ !site.exists ? 'Offline' : site.broken ? 'Broken' : 'Online' }}
                </span>
              </span>
            </h1>
            <Badge v-if="site.site_config?.ssl" label="SSL" theme="blue" />
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
          <Button variant="outline" @click="showLogin = true">
            Login to Site
          </Button>
          <Button variant="outline" :loading="actionLoading === 'backup'" @click="doAction('backup')">
            Backup
          </Button>
          <Button v-if="nginxEnabled && !site.site_config?.ssl" variant="outline" :loading="sslLoading" @click="enableSsl">
            Enable SSL
          </Button>
          <Button v-if="installable.length" variant="solid" @click="showInstall = true">
            Install App
          </Button>
        </div>
      </div>

      <ErrorMessage :message="actionError" />
      <ErrorMessage :message="sslError" />

      <!-- Tabs -->
      <Tabs :tabs="tabs" v-model="activeTab">
        <template #tab-panel="{ tab }">
          <!-- Apps -->
          <div v-if="tab.label === 'Apps'" class="pt-4">
            <div v-if="!site.installed_apps.length" class="py-10 text-center text-sm text-ink-gray-4">
              No apps installed on this site.
            </div>
            <div v-else class="divide-y rounded border">
              <div
                v-for="app in site.installed_apps"
                :key="app"
                class="flex items-center justify-between px-4 py-3"
              >
                <div class="flex items-center gap-3">
                  <div
                    class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md overflow-hidden"
                    :style="logoMap[app] ? {} : { background: hashColor(app) }"
                  >
                    <img v-if="logoMap[app]" :src="logoMap[app]" :alt="app" class="h-full w-full object-contain" />
                    <span v-else class="text-sm font-bold text-white">{{ app[0].toUpperCase() }}</span>
                  </div>
                  <span class="text-sm font-medium text-ink-gray-8">{{ app }}</span>
                </div>
                <Button variant="ghost" theme="red" size="sm" @click="confirmUninstall(app)">
                  Uninstall
                </Button>
              </div>
            </div>
          </div>

          <!-- Config -->
          <div v-else-if="tab.label === 'Config'" class="pt-4">
            <div class="rounded border bg-surface-gray-1 p-4">
              <div class="mb-3 flex items-center justify-between">
                <p class="text-xs font-medium text-ink-gray-5">site_config.json</p>
                <Button variant="ghost" size="sm" @click="openEditConfig">Edit</Button>
              </div>
              <div v-if="!site.site_config || !Object.keys(site.site_config).length" class="text-xs text-ink-gray-4">
                Empty config.
              </div>
              <ConfigTree v-else :data="site.site_config" class="font-mono text-xs" />
            </div>
          </div>

          <!-- Backups -->
          <div v-else-if="tab.label === 'Backups'" class="pt-4 flex flex-col gap-4">
            <!-- Schedule -->
            <div class="rounded border p-4">
              <h3 class="mb-3 text-sm font-semibold text-ink-gray-9">Backup Schedule</h3>
              <div v-if="scheduleLoading" class="text-sm text-ink-gray-5">Loading…</div>
              <div v-else class="flex flex-col gap-3">
                <p class="text-sm text-ink-gray-7">
                  <span v-if="currentSchedule">
                    Active:
                    <code class="rounded bg-surface-gray-2 px-1 py-0.5 font-mono text-xs">{{ currentSchedule }}</code>
                  </span>
                  <span v-else class="text-ink-gray-5">No scheduled backups.</span>
                </p>
                <div class="flex flex-wrap gap-1.5">
                  <button
                    v-for="p in schedulePresets"
                    :key="p.value"
                    class="rounded bg-surface-gray-2 px-2 py-1 text-xs text-ink-gray-7 hover:bg-surface-gray-3"
                    @click="scheduleInput = p.value"
                  >{{ p.label }}</button>
                </div>
                <div class="flex gap-2">
                  <input
                    v-model="scheduleInput"
                    placeholder="e.g. 0 2 * * *"
                    class="flex-1 rounded border px-2 py-1.5 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-gray-400"
                  />
                  <Button variant="outline" size="sm" :loading="scheduleSaving" :disabled="!scheduleInput" @click="saveSchedule">Save</Button>
                  <Button v-if="currentSchedule" variant="ghost" size="sm" theme="red" :loading="scheduleRemoving" @click="removeSchedule">Remove</Button>
                </div>
                <ErrorMessage :message="scheduleError" />
              </div>
            </div>

            <!-- History -->
            <div class="rounded border p-4">
              <div class="mb-3 flex items-center justify-between">
                <h3 class="text-sm font-semibold text-ink-gray-9">Backup History</h3>
                <Button variant="ghost" size="sm" @click="loadBackups">Refresh</Button>
              </div>
              <div v-if="backupsLoading" class="py-6 text-center text-sm text-ink-gray-5">Loading…</div>
              <div v-else-if="backupsError">
                <ErrorMessage :message="backupsError" />
              </div>
              <div v-else-if="!backups.length" class="py-10 text-center text-sm text-ink-gray-4">
                No backups found.
              </div>
              <div v-else class="flex flex-col gap-2">
                <div v-for="set in backups" :key="set.timestamp" class="rounded border p-3">
                  <div class="mb-2 flex items-center justify-between">
                    <p class="text-sm font-medium text-ink-gray-8">{{ formatBackupDate(set.created_at) }}</p>
                    <Button variant="ghost" theme="red" size="sm"
                      @click="deleteBackupTarget = set; showDeleteBackup = true">Delete</Button>
                  </div>
                  <div class="flex flex-col gap-1">
                    <div
                      v-for="file in set.files"
                      :key="file.filename"
                      class="flex items-center justify-between gap-2 text-xs"
                    >
                      <span class="truncate font-mono text-ink-gray-7">{{ file.filename }}</span>
                      <span class="shrink-0 text-ink-gray-4">{{ formatSize(file.size_bytes) }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Danger Zone -->
          <div v-else-if="tab.label === 'Danger Zone'" class="pt-4 flex flex-col gap-3">
            <div class="rounded border border-red-200 p-4">
              <div class="flex items-center justify-between gap-4">
                <div>
                  <p class="text-sm font-medium text-ink-gray-9">Drop Site</p>
                  <p class="mt-0.5 text-sm text-ink-gray-5">
                    Permanently delete <strong>{{ siteName }}</strong> and all its data. This cannot be undone.
                  </p>
                </div>
                <Button variant="solid" theme="red" class="shrink-0" @click="showDrop = true">
                  Drop Site
                </Button>
              </div>
            </div>
            <div v-if="site.broken" class="rounded border border-red-200 p-4">
              <div class="flex items-center justify-between gap-4">
                <div>
                  <p class="text-sm font-medium text-ink-gray-9">Force Delete</p>
                  <p class="mt-0.5 text-sm text-ink-gray-5">
                    This site is broken (database unreachable). Remove the site directory without running frappe cleanup.
                  </p>
                </div>
                <Button variant="solid" theme="red" class="shrink-0" @click="showForceDrop = true">
                  Force Delete
                </Button>
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
          <FormControl
            label="App to install"
            type="select"
            v-model="selectedInstallApp"
            :options="[{ label: 'Select an app…', value: '' }, ...installable.map(a => ({ label: a, value: a }))]"
          />
          <ErrorMessage :message="installError" class="mt-2" />
          <div class="mt-4 flex justify-end gap-2">
            <Button variant="ghost" @click="showInstall = false">Cancel</Button>
            <Button variant="solid" :loading="installLoading" :disabled="!selectedInstallApp" @click="installApp">Install</Button>
          </div>
        </div>
      </template>
    </Dialog>

    <!-- Drop Site dialog -->
    <Dialog v-model="showDrop" :options="{ title: 'Drop Site', size: 'sm' }">
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          Are you sure you want to permanently drop <strong>{{ siteName }}</strong>?
          All data will be lost and this cannot be undone.
        </p>
        <div class="mt-4 flex justify-end gap-2">
          <Button variant="ghost" @click="showDrop = false">Cancel</Button>
          <Button variant="solid" theme="red" :loading="actionLoading === 'drop'"
            @click="showDrop = false; doAction('drop')">Drop Site</Button>
        </div>
      </template>
    </Dialog>

    <!-- Force Drop dialog -->
    <Dialog v-model="showForceDrop" :options="{ title: 'Force Delete Site', size: 'sm' }">
      <template #body-content>
        <p class="text-sm text-ink-gray-7">
          Force delete <strong>{{ siteName }}</strong>? The site directory will be removed immediately without frappe cleanup. The database will <strong>not</strong> be dropped.
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
    <Dialog v-model="showLogin" :options="{ title: 'Login to Site', size: 'sm' }">
      <template #body-content>
        <div @pointerdown.stop>
          <FormControl
            label="Administrator password"
            type="password"
            v-model="loginPassword"
            placeholder="admin"
            @keydown.enter="loginToSite"
          />
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

    <!-- Edit Config dialog -->
    <Dialog v-model="showEditConfig" :options="{ title: 'Edit site_config.json', size: 'lg' }">
      <template #body-content>
        <div @pointerdown.stop>
          <textarea
            v-model="editConfigText"
            rows="20"
            class="w-full rounded border bg-surface-gray-1 p-3 font-mono text-sm text-ink-gray-8 focus:outline-none focus:ring-1 focus:ring-gray-400"
            spellcheck="false"
          />
          <ErrorMessage :message="editConfigError" class="mt-2" />
          <div class="mt-4 flex justify-end gap-2">
            <Button variant="ghost" @click="showEditConfig = false">Cancel</Button>
            <Button variant="solid" :loading="editConfigLoading" @click="saveConfig">Save</Button>
          </div>
        </div>
      </template>
    </Dialog>

    <!-- Delete Backup dialog -->
    <Dialog v-model="showDeleteBackup" :options="{ title: 'Delete Backup', size: 'sm' }">
      <template #body-content>
        <p class="text-sm leading-relaxed text-ink-gray-7">
          Delete the backup from <strong>{{ deleteBackupTarget ? formatBackupDate(deleteBackupTarget.created_at) : '' }}</strong>? This cannot be undone.
        </p>
        <ErrorMessage v-if="deleteBackupError" :message="deleteBackupError" class="mt-2" />
        <div class="mt-4 flex justify-end gap-2">
          <Button variant="ghost" @click="showDeleteBackup = false">Cancel</Button>
          <Button variant="solid" theme="red" :loading="deletingBackup" @click="deleteBackupSet">Delete</Button>
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
          <p class="mb-2 text-xs text-ink-gray-4">If the app is broken and normal uninstall fails, force-remove it from the site's app list without running any app scripts.</p>
          <ErrorMessage :message="forceUninstallError" class="mb-2" />
          <Button variant="outline" theme="red" size="sm" :loading="forceUninstallLoading" @click="doForceUninstall">
            Force Remove
          </Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
