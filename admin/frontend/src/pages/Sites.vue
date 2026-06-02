<script setup>
import { h, ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Badge, Dialog, ListView, FormControl, LoadingText, ErrorMessage, Switch, TabButtons } from 'frappe-ui'
import FilePickerField from '../components/FilePickerField.vue'

const router = useRouter()
const sites = ref([])
const registry = ref([])
const loading = ref(true)
const error = ref('')

const showCreate = ref(false)
const siteName = ref('')
const adminPassword = ref('')
const creating = ref(false)
const createError = ref('')
const restoreFromBackup = ref(false)
const restoreMode = ref('existing')
const backupSourceSite = ref('')
const loadingBackups = ref(false)
const backupSets = ref([])
const selectedBackupTs = ref('')
const uploadDb = ref(null)
const uploadPublic = ref(null)
const uploadPrivate = ref(null)

const logoMap = computed(() => Object.fromEntries(registry.value.map(a => [a.name, a.logo_url])))

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']
function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

function appLogoEl(appName) {
  const logo = logoMap.value[appName]
  return h('div', {
    class: 'flex h-5 w-5 shrink-0 items-center justify-center rounded overflow-hidden',
    style: logo ? {} : { background: hashColor(appName) },
  }, logo
    ? [h('img', { src: logo, alt: appName, class: 'h-full w-full object-contain' })]
    : [h('span', { class: 'text-[9px] font-bold text-white leading-none' }, appName[0].toUpperCase())]
  )
}

const columns = computed(() => [
  { label: 'Name', key: 'name', width: '200px' },
  {
    label: 'Status', key: '_status', width: '80px',
    prefix: ({ row }) => h(Badge, { label: row._status, theme: row._status === 'online' ? 'green' : 'gray' }),
    getLabel: () => '',
  },
  {
    label: 'Apps', key: '_apps',
    prefix: ({ row }) => h('div', { class: 'flex items-center gap-1 flex-wrap py-1' },
      row.installed_apps.map(app => appLogoEl(app))
    ),
    getLabel: () => '',
  },
  { label: 'Database', key: 'db_name', width: '150px' },
])

const rows = computed(() =>
  sites.value.map(s => ({
    ...s,
    _status: s.exists ? 'online' : 'offline',
  }))
)

async function load() {
  try {
    const res = await fetch('/api/sites/')
    sites.value = await res.json()
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

function formatBackupDate(isoStr) {
  return new Date(isoStr).toLocaleString()
}

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
        body: JSON.stringify({ name: siteName.value.trim(), admin_password: adminPassword.value.trim() }),
      })
    } else if (restoreMode.value === 'existing') {
      const set = backupSets.value.find(s => s.timestamp === selectedBackupTs.value)
      const db = set.files.find(f => f.kind === 'database')
      const pub = set.files.find(f => f.kind === 'files')
      const priv = set.files.find(f => f.kind === 'private-files')
      const body = { command: 'new-site-from-backup', name: siteName.value.trim(), db_file: db.path }
      if (adminPassword.value.trim()) body.admin_password = adminPassword.value.trim()
      if (pub) body.public_files = pub.path
      if (priv) body.private_files = priv.path
      res = await fetch('/api/tasks/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    } else {
      const fd = new FormData()
      fd.append('name', siteName.value.trim())
      fd.append('admin_password', adminPassword.value.trim())
      fd.append('db_file', uploadDb.value)
      if (uploadPublic.value) fd.append('public_files', uploadPublic.value)
      if (uploadPrivate.value) fd.append('private_files', uploadPrivate.value)
      res = await fetch('/api/sites/create-from-upload', { method: 'POST', body: fd })
    }
    const d = await res.json()
    if (d.ok) { showCreate.value = false; router.push(`/tasks/${d.task_id}`) }
    else createError.value = d.error
  } catch (e) {
    createError.value = e.message
  } finally {
    creating.value = false
  }
}

function openCreate() {
  showCreate.value = true
  siteName.value = ''
  adminPassword.value = ''
  createError.value = ''
  restoreFromBackup.value = false
  restoreMode.value = 'existing'
  backupSourceSite.value = ''
  backupSets.value = []
  selectedBackupTs.value = ''
  uploadDb.value = null
  uploadPublic.value = null
  uploadPrivate.value = null
}

onMounted(() => { load(); loadRegistry() })
</script>

<template>
  <div class="flex flex-col gap-4">
    <div class="flex justify-end">
      <Button variant="solid" @click="openCreate">Create Site</Button>
    </div>

    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <div v-else class="overflow-hidden">
      <ListView
        :columns="columns"
        :rows="rows"
        row-key="name"
        :options="{
          getRowRoute: (row) => `/sites/${row.name}`,
          selectable: false,
          showTooltip: false,
        }"
      />
    </div>

    <Dialog v-model="showCreate" :options="{ title: 'Create Site' }">
      <template #body-content>
        <div @pointerdown.stop class="flex flex-col gap-4">
          <FormControl label="Site Name" type="text" v-model="siteName" placeholder="mysite.localhost" @keyup.enter="createSite" />
          <FormControl label="Admin Password" type="password" v-model="adminPassword" placeholder="admin" description="Leave blank to use 'admin'" />

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
  </div>
</template>
