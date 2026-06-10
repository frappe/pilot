<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  Button, Badge, Dialog, FormControl,
  LoadingText, ErrorMessage, TextInput, Select,
} from 'frappe-ui'
const router = useRouter()
const apps = ref([])
const loading = ref(true)
const error = ref('')
const updateMap = ref({})  // name → { commits_behind, remote_commit }
const updateLoading = ref(false)

async function loadApps() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/apps/')
    if (!res.ok) throw new Error(`${res.status}`)
    apps.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadUpdateStatus() {
  try {
    const res = await fetch('/api/updates/')
    if (!res.ok) return
    const data = await res.json()
    updateMap.value = Object.fromEntries((data.apps || []).map(a => [a.name, a]))
  } catch {}
}

// Bench default branch
const defaultBranch = ref('')

async function loadDefaultBranch() {
  try {
    const res = await fetch('/api/settings/')
    if (!res.ok) return
    const data = await res.json()
    defaultBranch.value = data.bench?.default_branch || ''
  } catch {}
}

// Add app dialog
const showAdd = ref(false)
const addMode = ref('picker')
const registry = ref([])
const registrySearch = ref('')
const selectedApp = ref(null)
const pickerBranches = ref([])
const pickerActiveBranch = ref('')
const pickerBranchInput = ref('')
const manualName = ref('')
const manualRepo = ref('')
const manualBranches = ref([])
const manualActiveBranch = ref('')
const manualBranchInput = ref('')
const addLoading = ref(false)
const addError = ref('')

// Edit app dialog (upstream + branch + remove)
const showEdit = ref(false)
const editApp = ref(null)
const editRepo = ref('')
const editBranch = ref('')
const editLoading = ref(false)
const editError = ref('')
const editShowRemove = ref(false)
const editRemoveLoading = ref(false)
const editRemoveError = ref('')

const filteredRegistry = computed(() => {
  const q = registrySearch.value.toLowerCase()
  if (!q) return registry.value
  return registry.value.filter(a =>
    a.name.includes(q) ||
    (a.title || '').toLowerCase().includes(q) ||
    (a.description || '').toLowerCase().includes(q)
  )
})

const logoMap = computed(() => Object.fromEntries(registry.value.map(a => [a.name, a.logo_url])))
const titleMap = computed(() => Object.fromEntries(registry.value.map(a => [a.name, a.title])))


const activeBranchOptions = computed(() =>
  pickerBranches.value.map(b => ({ label: b, value: b }))
)
const manualActiveBranchOptions = computed(() =>
  manualBranches.value.map(b => ({ label: b, value: b }))
)

async function loadRegistry() {
  try {
    const res = await fetch('/api/apps/registry')
    registry.value = await res.json()
  } catch { registry.value = [] }
}

function openAdd() {
  showAdd.value = true
  addMode.value = 'picker'
  addError.value = ''
  selectedApp.value = null
  pickerBranches.value = []
  pickerActiveBranch.value = ''
  pickerBranchInput.value = ''
  registrySearch.value = ''
  manualName.value = ''
  manualRepo.value = ''
  manualBranches.value = defaultBranch.value ? [defaultBranch.value] : []
  manualActiveBranch.value = defaultBranch.value
  manualBranchInput.value = ''
  if (!registry.value.length) loadRegistry()
}

function selectRegistryApp(a) {
  selectedApp.value = a
  pickerBranches.value = a.branches ? [...a.branches] : (a.branch ? [a.branch] : [])
  const preferred = defaultBranch.value && pickerBranches.value.includes(defaultBranch.value)
    ? defaultBranch.value
    : pickerBranches.value[0] || a.branch || ''
  pickerActiveBranch.value = preferred
}

function addPickerBranch() {
  const val = pickerBranchInput.value.trim()
  if (val && !pickerBranches.value.includes(val)) {
    pickerBranches.value.push(val)
    if (!pickerActiveBranch.value) pickerActiveBranch.value = val
  }
  pickerBranchInput.value = ''
}

function onPickerBranchKeydown(e) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault()
    addPickerBranch()
  }
}

function addManualBranch() {
  const val = manualBranchInput.value.trim()
  if (val && !manualBranches.value.includes(val)) {
    manualBranches.value.push(val)
    if (!manualActiveBranch.value) manualActiveBranch.value = val
  }
  manualBranchInput.value = ''
}

function onManualBranchKeydown(e) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault()
    addManualBranch()
  }
}


function openEdit(app) {
  editApp.value = app
  editRepo.value = app.repo
  editBranch.value = app.branch
  editError.value = ''
  editShowRemove.value = false
  editRemoveError.value = ''
  showEdit.value = true
}

async function saveEdit() {
  const app = editApp.value
  editError.value = ''
  const newRepo = editRepo.value.trim()
  const newBranch = editBranch.value.trim()
  if (newRepo && newRepo !== app.repo && !isValidRepoUrl(newRepo)) {
    editError.value = 'Repository URL must be a valid git URL (https://, git@host:path, or local path).'
    return
  }
  if (newBranch && newBranch !== app.branch && !isValidBranch(newBranch)) {
    editError.value = "Branch name may only contain letters, numbers, hyphens, underscores, dots, and slashes."
    return
  }
  editLoading.value = true
  try {
    if (newRepo && newRepo !== app.repo) {
      const res = await fetch(`/api/apps/${app.name}/set-upstream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo: newRepo }),
      })
      const d = await res.json()
      if (!d.ok) { editError.value = d.error; return }
    }
    if (newBranch && newBranch !== app.branch) {
      const res = await fetch(`/api/apps/${app.name}/switch-branch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch: newBranch }),
      })
      const d = await res.json()
      if (!d.ok) { editError.value = d.error; return }
      showEdit.value = false
      router.push(`/tasks/${d.task_id}`)
      return
    }
    showEdit.value = false
    loadApps()
  } catch (e) {
    editError.value = e.message
  } finally {
    editLoading.value = false
  }
}

async function doEditRemove() {
  editRemoveLoading.value = true
  editRemoveError.value = ''
  try {
    const res = await fetch(`/api/apps/${editApp.value.name}/remove`, { method: 'POST' })
    const d = await res.json()
    if (d.ok) { showEdit.value = false; router.push(`/tasks/${d.task_id}`) }
    else editRemoveError.value = d.error
  } catch (e) {
    editRemoveError.value = e.message
  } finally {
    editRemoveLoading.value = false
  }
}

function isValidRepoUrl(url) {
  return /^(https?:\/\/.+|git@.+:.+|[/~].*|\.\.\/.*)/.test(url.trim())
}

function isValidBranch(branch) {
  return branch && !/\.\./.test(branch) && /^[A-Za-z0-9._/\-]+$/.test(branch)
}

async function doAdd(name, repo, branch, branches) {
  addError.value = ''
  if (addMode.value === 'manual') {
    if (!name.trim()) { addError.value = 'App name is required.'; return }
    if (!/^[A-Za-z][A-Za-z0-9_\-]*$/.test(name.trim())) {
      addError.value = 'App name must start with a letter and contain only letters, numbers, hyphens, and underscores.'
      return
    }
    if (!repo.trim()) { addError.value = 'Repository URL is required.'; return }
    if (!isValidRepoUrl(repo)) {
      addError.value = 'Repository URL must be a valid git URL (https://, git@host:path, or local path).'
      return
    }
    if (branch && !isValidBranch(branch)) {
      addError.value = "Branch name may only contain letters, numbers, hyphens, underscores, dots, and slashes."
      return
    }
  }
  addLoading.value = true
  try {
    const res = await fetch('/api/apps/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, repo, branch, branches }),
    })
    const d = await res.json()
    if (d.ok) { showAdd.value = false; router.push(`/tasks/${d.task_id}`) }
    else addError.value = d.error
  } catch (e) {
    addError.value = e.message
  } finally {
    addLoading.value = false
  }
}

const COLORS = ['#4f46e5','#0891b2','#059669','#d97706','#dc2626','#7c3aed']
function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

async function runUpdate() {
  updateLoading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'update' }),
    })
    const d = await res.json()
    if (d.ok) router.push(`/tasks/${d.task_id}`)
    else error.value = d.error
  } catch (e) {
    error.value = e.message
  } finally {
    updateLoading.value = false
  }
}

onMounted(() => { loadApps(); loadRegistry(); loadUpdateStatus(); loadDefaultBranch() })
</script>

<template>
  <div class="mx-auto flex max-w-2xl flex-col gap-4">
    <div class="flex justify-end gap-2">
      <Button variant="outline" :loading="updateLoading" @click="runUpdate">Update Bench</Button>
      <Button variant="solid" @click="openAdd">Add App</Button>
    </div>

    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <div v-else class="flex flex-col gap-2">
      <p v-if="!apps.length" class="py-8 text-center text-sm text-ink-gray-4">No apps installed.</p>
      <div
        v-for="a in apps"
        :key="a.name"
        class="flex items-start gap-3 rounded-lg border border-outline-gray-1 bg-surface-white px-4 py-3 shadow-sm"
      >
        <!-- Logo -->
        <div
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded overflow-hidden"
          :style="logoMap[a.name] ? {} : { background: hashColor(a.name) }"
        >
          <img v-if="logoMap[a.name]" :src="logoMap[a.name]" :alt="a.name" class="h-full w-full object-contain" />
          <span v-else class="text-xs font-bold text-white">{{ a.name[0].toUpperCase() }}</span>
        </div>

        <!-- Info -->
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="font-medium text-ink-gray-9">{{ titleMap[a.name] || a.name }}</span>
            <Badge :label="a.branch || '—'" theme="gray" variant="subtle" />
            <Badge v-if="a.uncommitted_changes" label="dirty" theme="orange" />
          </div>
          <p class="mt-0.5 truncate text-xs text-ink-gray-4">{{ a.repo }}</p>
        </div>

        <!-- Update status -->
        <div class="shrink-0">
          <template v-if="updateMap[a.name]">
            <Badge
              v-if="updateMap[a.name].commits_behind > 0"
              :label="`${updateMap[a.name].commits_behind} behind`"
              theme="yellow"
            />
            <Badge v-else label="Up to date" theme="green" />
          </template>
        </div>

        <!-- Edit button -->
        <Button variant="outline" size="sm" @click="openEdit(a)">Edit</Button>
      </div>
    </div>

    <!-- Add App dialog -->
    <Dialog v-model="showAdd" :options="{ title: 'Add App', size: 'lg' }">
      <template #body-content>
        <!-- @pointerdown.stop prevents the overlay's preventDefault from blocking input focus -->
        <div @pointerdown.stop>

        <!-- Registry picker mode -->
        <div v-if="addMode === 'picker'">
          <TextInput v-model="registrySearch" placeholder="Search apps…" class="mb-3" />
          <div class="max-h-52 overflow-y-auto mb-3">
            <div v-if="!filteredRegistry.length" class="p-4 text-ink-gray-4">No apps found</div>
            <button
              v-for="a in filteredRegistry"
              :key="a.name"
              class="flex w-full items-center gap-3 px-3 py-2 rounded hover:bg-surface-gray-2"
              :class="{ 'bg-surface-blue-1': selectedApp?.name === a.name }"
              @click="selectRegistryApp(a)"
            >
              <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded overflow-hidden"
                   :style="a.logo_url ? {} : { background: hashColor(a.name) }">
                <img v-if="a.logo_url" :src="a.logo_url" :alt="a.title || a.name" class="h-full w-full object-contain" />
                <span v-else class="font-bold text-white">{{ (a.title || a.name)[0].toUpperCase() }}</span>
              </div>
              <div class="text-left flex-1 min-w-0">
                <div class="font-medium text-sm text-ink-gray-9">{{ a.title || a.name }}</div>
                <div class="text-xs text-ink-gray-4 truncate">{{ a.description }}</div>
              </div>
              <div v-if="a.branches?.length" class="flex gap-1 shrink-0">
                <Badge v-for="b in a.branches" :key="b" :label="b" theme="gray" variant="outline" />
              </div>
            </button>
          </div>

          <!-- Branch configuration for selected app -->
          <div v-if="selectedApp" class="border-t border-outline-gray-1 pt-3 flex flex-col gap-3">
            <div>
              <p class="mb-1.5 text-xs text-ink-gray-5">Custom Branch</p>
              <div class="flex gap-2">
                <TextInput
                  class="flex-1"
                  v-model="pickerBranchInput"
                  placeholder="e.g. version-14, develop"
                  @keydown="onPickerBranchKeydown"
                />
                <Button variant="subtle" @click="addPickerBranch" :disabled="!pickerBranchInput.trim()">Add</Button>
              </div>
            </div>
            <Select
              label="Active Branch"
              :options="activeBranchOptions"
              v-model="pickerActiveBranch"
              placeholder="Select active branch"
            />
          </div>

          <ErrorMessage :message="addError" class="mt-2" />
          <div class="mt-4 flex justify-between">
            <Button variant="ghost" @click="addMode = 'manual'">Enter manually</Button>
            <div class="flex gap-2">
              <Button variant="ghost" @click="showAdd = false">Cancel</Button>
              <Button
                variant="solid"
                :loading="addLoading"
                :disabled="!selectedApp"
                @click="doAdd(selectedApp.name, selectedApp.repo, pickerActiveBranch || selectedApp.branch || '', pickerBranches)"
              >
                Add App
              </Button>
            </div>
          </div>
        </div>

        <!-- Manual entry mode -->
        <div v-else>
          <div class="flex flex-col gap-3">
            <FormControl label="Name" type="text" v-model="manualName" placeholder="my_app" />
            <FormControl label="Repository URL" type="text" v-model="manualRepo" placeholder="https://github.com/org/repo" />
            <div>
              <p class="mb-1.5 text-xs text-ink-gray-5">Add Branch</p>
              <div class="flex gap-2">
                <TextInput
                  class="flex-1"
                  v-model="manualBranchInput"
                  placeholder="e.g. main, develop"
                  @keydown="onManualBranchKeydown"
                />
                <Button variant="subtle" @click="addManualBranch" :disabled="!manualBranchInput.trim()">Add</Button>
              </div>
            </div>
            <Select
              v-if="manualBranches.length"
              label="Active Branch"
              :options="manualActiveBranchOptions"
              v-model="manualActiveBranch"
              placeholder="Select active branch"
            />
          </div>

          <ErrorMessage :message="addError" class="mt-2" />
          <div class="mt-4 flex justify-between">
            <Button variant="ghost" @click="addMode = 'picker'">← Back to registry</Button>
            <div class="flex gap-2">
              <Button variant="ghost" @click="showAdd = false">Cancel</Button>
              <Button
                variant="solid"
                :loading="addLoading"
                @click="doAdd(manualName, manualRepo, manualActiveBranch || manualBranches[0] || '', manualBranches)"
              >
                Add App
              </Button>
            </div>
          </div>
        </div>

        </div> <!-- end @pointerdown.stop wrapper -->
      </template>
    </Dialog>

    <!-- Edit App dialog -->
    <Dialog v-model="showEdit" :options="{ title: editApp?.name || 'Edit App', size: 'sm' }">
      <template #body-content>
        <div @pointerdown.stop class="flex flex-col gap-4">
          <FormControl label="Upstream URL" v-model="editRepo" placeholder="https://github.com/org/repo" />
          <FormControl label="Branch" v-model="editBranch" placeholder="e.g. version-15, develop" />

          <ErrorMessage :message="editError" />

          <!-- Danger zone -->
          <div class="border-t border-outline-gray-1 pt-3">
            <template v-if="!editShowRemove">
              <Button variant="outline" theme="red" @click="editShowRemove = true">Remove App</Button>
            </template>
            <template v-else>
              <p class="mb-3 text-sm text-ink-gray-5">
                Remove <strong>{{ editApp?.name }}</strong>? This will uninstall it from all sites and delete the app directory.
              </p>
              <ErrorMessage :message="editRemoveError" />
              <div class="mt-2 flex gap-2">
                <Button variant="ghost" @click="editShowRemove = false">Cancel</Button>
                <Button variant="solid" theme="red" :loading="editRemoveLoading" @click="doEditRemove">Remove</Button>
              </div>
            </template>
          </div>

          <div class="flex justify-end gap-2 border-t border-outline-gray-1 pt-3">
            <Button variant="ghost" @click="showEdit = false">Cancel</Button>
            <Button variant="solid" :loading="editLoading" @click="saveEdit">Save</Button>
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>
