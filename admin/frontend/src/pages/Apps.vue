<script setup>
import { h, ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  Button, Badge, Dialog, ListView, FormControl,
  LoadingText, ErrorMessage, TextInput, Select,
} from 'frappe-ui'

const router = useRouter()
const apps = ref([])
const loading = ref(true)
const error = ref('')

// Add app dialog
const showAdd = ref(false)
const addMode = ref('picker')
const registry = ref([])
const registrySearch = ref('')
const selectedApp = ref(null)
const pickerBranches = ref([])        // string[] — the branches to store
const pickerActiveBranch = ref('')    // the chosen active branch
const pickerBranchInput = ref('')     // free-form input for adding a branch
const manualName = ref('')
const manualRepo = ref('')
const manualBranches = ref([])        // string[]
const manualActiveBranch = ref('')
const manualBranchInput = ref('')
const addLoading = ref(false)
const addError = ref('')

// Switch branch dialog
const showSwitch = ref(false)
const switchApp = ref(null)
const switchBranch = ref('')
const switchLoading = ref(false)
const switchError = ref('')

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

const columns = computed(() => [
  {
    label: 'App', key: 'name', width: '180px',
    prefix: ({ row }) => {
      const logo = logoMap.value[row.name]
      return h('div', {
        class: 'flex h-6 w-6 shrink-0 items-center justify-center rounded overflow-hidden mr-1',
        style: logo ? {} : { background: hashColor(row.name) },
      }, logo
        ? [h('img', { src: logo, alt: row.name, class: 'h-full w-full object-contain' })]
        : [h('span', { class: 'text-xs font-bold text-white' }, row.name[0].toUpperCase())]
      )
    },
  },
  { label: 'Repo', key: 'repo' },
  {
    label: 'Branch',
    key: '_branch',
    width: '200px',
    prefix: ({ row }) => h('div', { class: 'flex items-center gap-1 flex-wrap py-1' },
      row._branchChips.map(b =>
        h(Badge, {
          label: b,
          theme: b === row.branch ? 'blue' : 'gray',
          variant: b === row.branch ? 'subtle' : 'outline',
          class: b !== row.branch ? 'cursor-pointer hover:bg-surface-gray-2' : '',
          onClick: () => b !== row.branch && openSwitch(row, b),
        })
      )
    ),
    getLabel: () => '',
  },
  {
    label: 'Status', key: '_status', width: '90px',
    prefix: ({ row }) => h(Badge, { label: row._status, theme: row._status === 'dirty' ? 'orange' : 'gray' }),
    getLabel: () => '',
  },
  { label: 'Version', key: 'installed_version', width: '90px' },
])

const rows = computed(() =>
  apps.value.map(a => ({
    ...a,
    _commit: a.is_cloned ? a.current_commit : 'not cloned',
    _status: a.uncommitted_changes ? 'dirty' : 'clean',
    _branchChips: a.branches && a.branches.length > 0 ? a.branches : [a.branch],
  }))
)

const activeBranchOptions = computed(() =>
  pickerBranches.value.map(b => ({ label: b, value: b }))
)
const manualActiveBranchOptions = computed(() =>
  manualBranches.value.map(b => ({ label: b, value: b }))
)

async function load() {
  try {
    const res = await fetch('/api/apps/')
    apps.value = await res.json()
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
  manualBranches.value = []
  manualActiveBranch.value = ''
  manualBranchInput.value = ''
  if (!registry.value.length) loadRegistry()
}

function selectRegistryApp(a) {
  selectedApp.value = a
  pickerBranches.value = a.branches ? [...a.branches] : (a.branch ? [a.branch] : [])
  pickerActiveBranch.value = a.branch || pickerBranches.value[0] || ''
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


function openSwitch(app, branch) {
  switchApp.value = app
  switchBranch.value = branch
  switchError.value = ''
  showSwitch.value = true
}

async function doAdd(name, repo, branch, branches) {
  addLoading.value = true
  addError.value = ''
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

async function doSwitch() {
  switchLoading.value = true
  switchError.value = ''
  try {
    const res = await fetch(`/api/apps/${switchApp.value.name}/switch-branch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ branch: switchBranch.value }),
    })
    const d = await res.json()
    if (d.ok) { showSwitch.value = false; router.push(`/tasks/${d.task_id}`) }
    else switchError.value = d.error
  } catch (e) {
    switchError.value = e.message
  } finally {
    switchLoading.value = false
  }
}

const COLORS = ['#4f46e5','#0891b2','#059669','#d97706','#dc2626','#7c3aed']
function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

onMounted(() => { load(); loadRegistry() })
</script>

<template>
  <div class="flex flex-col gap-4">
    <div class="flex justify-end">
      <Button variant="solid" @click="openAdd">Add App</Button>
    </div>

    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <div v-else class="overflow-hidden">
      <ListView
        :columns="columns"
        :rows="rows"
        row-key="name"
        :options="{ selectable: false, showTooltip: false }"
      />
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
            <div v-if="!filteredRegistry.length" class="p-4 text-gray-400">No apps found</div>
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
                <div class="font-medium text-sm">{{ a.title || a.name }}</div>
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

    <!-- Switch Branch confirmation dialog -->
    <Dialog v-model="showSwitch" :options="{ title: 'Switch Branch' }">
      <template #body-content>
        <p v-if="switchApp" class="text-sm">
          Switch <strong>{{ switchApp.name }}</strong> from
          <Badge :label="switchApp.branch" theme="gray" />
          to
          <Badge :label="switchBranch" theme="blue" />?
        </p>
        <p class="mt-1 text-sm text-ink-gray-4">
          This will run git checkout, reinstall the app, and rebuild its assets.
        </p>
        <ErrorMessage :message="switchError" class="mt-2" />
        <div class="mt-4 flex justify-end gap-2">
          <Button variant="ghost" @click="showSwitch = false">Cancel</Button>
          <Button variant="solid" :loading="switchLoading" @click="doSwitch">Switch Branch</Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
