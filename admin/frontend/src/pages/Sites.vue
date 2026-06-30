<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRouter, RouterLink } from 'vue-router'
import { Button, Dialog, FormControl, LoadingText, ErrorMessage, Switch, TabButtons, Select } from 'frappe-ui'
import FilePickerField from '../components/FilePickerField.vue'
import UpdateAppDialog from '../components/UpdateAppDialog.vue'
import { useTaskProgress } from '../composables/useTaskProgress.js'
import { useAppRegistry, hashColor } from '../composables/useAppRegistry.js'

const router = useRouter()
const { watchTask } = useTaskProgress()
const { registry, logoMap, loadRegistry } = useAppRegistry()
const sites = ref([])
const loading = ref(true)
const error = ref('')

async function loadSites() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/sites/')
    if (!res.ok) throw new Error(`${res.status}`)
    sites.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
async function loadWildcardDomains() {
  try {
    const res = await fetch('/api/sites/wildcard-domains')
    const d = await res.json()
    wildcardDomains.value = d.domains || []
    selectedSuffix.value = wildcardDomains.value[0] || ''
  } catch {
    wildcardDomains.value = []
  }
}

const showCreate = ref(false)
const pendingTaskId = ref('')
const siteName = ref('')
const sitePrefix = ref('')
const wildcardDomains = ref([])
const selectedSuffix = ref('')
const creating = ref(false)
const createError = ref('')
const benchDbType = ref('')
const siteDbType = ref('')
const restoreFromBackup = ref(false)
const restoreMode = ref('existing')
const backupSourceSite = ref('')
const loadingBackups = ref(false)
const backupSets = ref([])
const selectedBackupTs = ref('')
const uploadDb = ref(null)
const uploadPublic = ref(null)
const uploadPrivate = ref(null)
const showConvertConfirm = ref(false)
const convertInfo = ref(null)
const converting = ref(false)

function siteStatus(s) {
  return !s.exists ? 'offline' : s.broken ? 'broken' : 'online'
}

const STATUS_DOT = { online: 'bg-surface-green-3', broken: 'bg-surface-red-4', offline: 'bg-ink-gray-3' }

function formatBackupDate(isoStr) {
  return new Date(isoStr).toLocaleString()
}

// In wildcard mode the visible field is just the prefix; keep siteName (what
// createSite() actually submits) assembled from prefix + chosen suffix.
watch([sitePrefix, selectedSuffix], () => {
  if (wildcardDomains.value.length > 0) {
    siteName.value = `${sitePrefix.value.trim()}${selectedSuffix.value}`
  }
})

watch(backupSourceSite, async (site) => {
  selectedBackupTs.value = ''
  backupSets.value = []
  if (!site) return
  loadingBackups.value = true
  try {
    const res = await fetch(`/api/sites/${encodeURIComponent(site)}/backups`)
    backupSets.value = await res.json()
  } catch { backupSets.value = [] }
  finally { loadingBackups.value = false }
})

async function createSite() {
  if (!siteName.value.trim()) { createError.value = 'Site name is required.'; return }
  if (!/^[a-zA-Z0-9][a-zA-Z0-9\-.]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$/.test(siteName.value.trim())) {
    createError.value = 'Site name must be a valid hostname (letters, numbers, hyphens, and dots only).'
    return
  }
  if (restoreFromBackup.value) {
    if (restoreMode.value === 'existing') {
      if (!backupSourceSite.value) { createError.value = 'Select a source site.'; return }
      if (!selectedBackupTs.value) { createError.value = 'Select a backup.'; return }
    } else if (!uploadDb.value) {
      createError.value = 'Database backup file is required.'
      return
    }
  }
  creating.value = true
  createError.value = ''
  try {
    let res
    if (!restoreFromBackup.value) {
      res = await fetch('/api/sites/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: siteName.value.trim(), db_type: siteDbType.value }),
      })
    } else if (restoreMode.value === 'existing') {
      const set = backupSets.value.find(s => s.timestamp === selectedBackupTs.value)
      const db = set.files.find(f => f.kind === 'database')
      const pub = set.files.find(f => f.kind === 'public-file')
      const priv = set.files.find(f => f.kind === 'private-file')
      const body = { command: 'new-site-from-backup', name: siteName.value.trim(), db_file: db.path }
      if (pub) body.public_files = pub.path
      if (priv) body.private_files = priv.path
      res = await fetch('/api/tasks/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    } else {
      const fd = new FormData()
      fd.append('name', siteName.value.trim())
      fd.append('db_file', uploadDb.value)
      if (uploadPublic.value) fd.append('public_files', uploadPublic.value)
      if (uploadPrivate.value) fd.append('private_files', uploadPrivate.value)
      res = await fetch('/api/sites/create-from-upload', { method: 'POST', body: fd })
    }
    const d = await res.json()
    // Navigate only after the dialog's leave transition completes (see
    // onCreateClosed); closing and routing in the same tick unmounts this page
    // mid-transition and orphans the teleported dialog overlay.
    if (d.needs_conversion_confirm) { convertInfo.value = d; showConvertConfirm.value = true }
    else if (d.ok) { pendingTaskId.value = d.task_id; showCreate.value = false }
    else createError.value = d.error
  } catch (e) {
    createError.value = e.message
  } finally {
    creating.value = false
  }
}

async function confirmConvert() {
  converting.value = true
  createError.value = ''
  try {
    const info = convertInfo.value
    const body = { command: 'new-site-from-backup', name: info.name, db_file: info.db_file, convert: true }
    if (adminPassword.value.trim()) body.admin_password = adminPassword.value.trim()
    if (info.public_files) body.public_files = info.public_files
    if (info.private_files) body.private_files = info.private_files
    const res = await fetch('/api/tasks/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const d = await res.json()
    if (d.ok) {
      showConvertConfirm.value = false
      pendingTaskId.value = d.task_id
      showCreate.value = false
    } else {
      createError.value = d.error
      showConvertConfirm.value = false
    }
  } catch (e) {
    createError.value = e.message
    showConvertConfirm.value = false
  } finally {
    converting.value = false
  }
}

async function cancelConvert() {
  const path = convertInfo.value?.db_file
  showConvertConfirm.value = false
  convertInfo.value = null
  // Best-effort: remove the uploaded backup we staged but won't restore.
  if (path) {
    try {
      await fetch('/api/sites/discard-upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ db_file: path }),
      })
    } catch (e) { /* ignore */ }
  }
}

function onCreateClosed() {
  if (!pendingTaskId.value) return
  const taskId = pendingTaskId.value
  pendingTaskId.value = ''
  watchTask(taskId)
}

function openCreate() {
  showCreate.value = true
  siteName.value = ''
  sitePrefix.value = ''
  loadWildcardDomains()
  createError.value = ''
  benchDbType.value = ''
  siteDbType.value = ''
  fetch('/api/status').then(r => r.json()).then(d => {
    benchDbType.value = d.db_type || 'mariadb'
    siteDbType.value = benchDbType.value
  }).catch(() => {})
  restoreFromBackup.value = false
  restoreMode.value = 'existing'
  backupSourceSite.value = ''
  backupSets.value = []
  selectedBackupTs.value = ''
  uploadDb.value = null
  uploadPublic.value = null
  uploadPrivate.value = null
  showConvertConfirm.value = false
  convertInfo.value = null
}

// App update indicator
const appsWithUpdates = ref([])
const checkingUpdates = ref(false)
const showUpdate = ref(false)

async function checkAppUpdates() {
  checkingUpdates.value = true
  try {
    const { task_id } = await fetch('/api/apps/fetch', { method: 'POST' }).then(r => r.json())
    while (true) {
      await new Promise(r => setTimeout(r, 1500))
      const { task, output } = await fetch(`/api/tasks/${task_id}`).then(r => r.json())
      if (task.status === 'running') continue
      if (task.status === 'success' && output?.length) {
        const updates = JSON.parse(output[output.length - 1])
        appsWithUpdates.value = Object.entries(updates)
          .filter(([, hasUpdate]) => hasUpdate)
          .map(([name]) => ({ name }))
      }
      break
    }
  } catch { /* best-effort */ }
  finally { checkingUpdates.value = false }
}

const updateError = ref('')

onMounted(() => { loadSites(); loadRegistry(); checkAppUpdates() })
</script>

<template>
  <div class="mx-auto flex max-w-2xl flex-col gap-4 mt-4">
    <!-- defer: after login, this page mounts in the same render pass as the
         AppLayout header, before #header-actions is attached to the document -->
    <Teleport defer to="#header-actions">
      <Button variant="outline" :loading="checkingUpdates" @click="showUpdate = true">
        <template #prefix>
          <span v-if="appsWithUpdates.length"
            class="relative flex h-2 w-2 shrink-0">
            <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
            <span class="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
          </span>
        </template>
        Update Bench
      </Button>
      <Button variant="outline" @click="openCreate">Create Site</Button>
    </Teleport>
    <ErrorMessage v-if="updateError" :message="updateError" />

    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <div v-else class="rounded-lg border border-outline-gray-1 overflow-hidden">
      <p v-if="!sites.length" class="py-10 text-center text-sm text-ink-gray-4">No sites yet.</p>
      <RouterLink
        v-for="s in sites"
        :key="s.name"
        :to="`/sites/${s.name}`"
        class="flex items-center gap-4 border-b border-outline-gray-1 last:border-b-0 bg-surface-white px-4 py-5 transition-colors hover:bg-surface-gray-1 no-underline"
      >
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="font-medium text-ink-gray-9 truncate">{{ s.name }}</span>
            <span
              class="group relative inline-flex h-2 w-2 shrink-0 self-center rounded-full"
              :class="STATUS_DOT[siteStatus(s)]"
            >
              <span class="pointer-events-none absolute bottom-full left-1/2 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded bg-ink-gray-9 px-1.5 py-0.5 text-[10px] text-surface-white opacity-0 transition-opacity group-hover:opacity-100">
                {{ siteStatus(s) }}
              </span>
            </span>
          </div>
        </div>
        <div v-if="s.installed_apps?.length" class="flex items-center gap-2 shrink-0">
          <div
            v-for="app in s.installed_apps"
            :key="app"
            class="flex h-7 w-7 shrink-0 items-center justify-center rounded overflow-hidden"
            :style="logoMap[app] ? {} : { background: hashColor(app) }"
          >
            <img v-if="logoMap[app]" :src="logoMap[app]" :alt="app" class="h-full w-full object-contain" />
            <span v-else class="text-xs font-bold text-white leading-none">{{ app[0].toUpperCase() }}</span>
          </div>
        </div>
      </RouterLink>
    </div>

    <UpdateAppDialog v-model="showUpdate" :apps="appsWithUpdates" />

    <Dialog v-model="showCreate" :options="{ title: 'Create Site' }" @after-leave="onCreateClosed">
      <template #body-content>
        <div @pointerdown.stop class="flex flex-col gap-4">
          <FormControl v-if="wildcardDomains.length === 0" label="Site Name" type="text" v-model="siteName"
            placeholder="mysite.localhost" @keyup.enter="createSite" />
          <div v-else>
            <span class="mb-1.5 block text-xs text-ink-gray-5">Site Name</span>
            <div class="flex items-stretch gap-2">
              <FormControl class="min-w-0 flex-1" type="text" v-model="sitePrefix" placeholder="mysite" @keyup.enter="createSite" />
              <Select v-if="wildcardDomains.length > 1" class="w-48 shrink-0" v-model="selectedSuffix"
                :options="wildcardDomains.map(d => ({ label: d, value: d }))" />
              <span v-else class="flex shrink-0 items-center whitespace-nowrap text-sm text-ink-gray-6">{{ wildcardDomains[0] }}</span>
            </div>
          </div>
          <div v-if="siteDbType">
            <span class="mb-1.5 block text-xs text-ink-gray-5">Database</span>
            <div class="grid grid-cols-2 gap-2">
              <button
                type="button"
                class="rounded-lg border p-3 text-left transition-colors"
                :class="siteDbType !== 'sqlite' ? 'border-outline-gray-3 bg-surface-gray-2' : 'border-outline-gray-2 hover:bg-surface-gray-1'"
                @click="siteDbType = benchDbType !== 'sqlite' ? benchDbType : 'mariadb'"
              >
                <span class="block text-sm font-medium text-ink-gray-9">{{ benchDbType === 'postgres' ? 'PostgreSQL' : 'MariaDB' }}</span>
                <span class="block text-xs text-ink-gray-5">Recommended</span>
              </button>
              <button
                type="button"
                class="rounded-lg border p-3 text-left transition-colors"
                :class="siteDbType === 'sqlite' ? 'border-outline-gray-3 bg-surface-gray-2' : 'border-outline-gray-2 hover:bg-surface-gray-1'"
                @click="siteDbType = 'sqlite'"
              >
                <span class="block text-sm font-medium text-ink-gray-9">SQLite</span>
                <span class="block text-xs text-ink-gray-5">Experimental</span>
              </button>
            </div>
          </div>

          <div class="border-t pt-4">
            <Switch v-model="restoreFromBackup" label="Restore from backup" />

            <div v-if="restoreFromBackup" class="mt-4 flex flex-col gap-4">
              <TabButtons
                v-model="restoreMode"
                :buttons="[
                  { label: 'From this bench', value: 'existing' },
                  { label: 'Upload files', value: 'upload' },
                ]"
              />

              <template v-if="restoreMode === 'existing'">
                <FormControl
                  label="Source Site"
                  type="select"
                  v-model="backupSourceSite"
                  :options="[{ label: '— select site —', value: '' }, ...sites.map(s => ({ label: s.name, value: s.name }))]"
                />
                <div v-if="backupSourceSite">
                  <LoadingText v-if="loadingBackups" />
                  <FormControl
                    v-else
                    label="Backup"
                    type="select"
                    v-model="selectedBackupTs"
                    :options="[{ label: '— select backup —', value: '' }, ...backupSets.map(s => ({ label: formatBackupDate(s.created_at), value: s.timestamp }))]"
                  />
                </div>
              </template>

              <template v-else>
                <FilePickerField
                  label="Database backup (.sql.gz)"
                  required
                  accept=".gz"
                  :file="uploadDb"
                  @change="uploadDb = $event"
                />
                <FilePickerField
                  label="Public files (.tar.gz)"
                  accept=".gz"
                  :file="uploadPublic"
                  @change="uploadPublic = $event"
                />
                <FilePickerField
                  label="Private files (.tar.gz)"
                  accept=".gz"
                  :file="uploadPrivate"
                  @change="uploadPrivate = $event"
                />
              </template>
            </div>
          </div>

          <ErrorMessage v-if="createError" :message="createError" />
          <div class="flex justify-end gap-2">
            <Button variant="ghost" @click="showCreate = false">Cancel</Button>
            <Button variant="solid" :loading="creating" @click="createSite">Create Site</Button>
          </div>
        </div>
      </template>
    </Dialog>

    <Dialog v-model="showConvertConfirm" :options="{ title: 'Convert MariaDB backup?' }">
      <template #body-content>
        <div class="flex flex-col gap-4">
          <p class="whitespace-pre-line text-base text-ink-gray-7">{{ convertInfo?.message }}</p>
          <ErrorMessage v-if="createError" :message="createError" />
          <div class="flex justify-end gap-2">
            <Button variant="ghost" @click="cancelConvert">Cancel</Button>
            <Button variant="solid" theme="red" :loading="converting" @click="confirmConvert">Convert &amp; Restore</Button>
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>
