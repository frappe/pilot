<script setup>
import { ref, computed } from 'vue'
import { Button, Badge, Dialog, FormControl, TextInput, ErrorMessage } from 'frappe-ui'
import { useTaskProgress } from '../composables/useTaskProgress.js'
import LucideTriangleAlert from '~icons/lucide/triangle-alert'

const props = defineProps({
  modelValue: Boolean,
  siteName: { type: String, required: true },
  registry: { type: Array, default: () => [] },
  installable: { type: Array, default: () => [] },
  installedApps: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

const { watchTask } = useTaskProgress()

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const INSTALL_CATEGORIES = ['All', 'Applications', 'Extensions', 'Integrations', 'Compliance', 'Developer Tools', 'Utilities']

// View modes: 'browse' (default), 'confirm' (a registry app picked), 'custom' (manual repo + branch).
const mode = ref('browse')
const search = ref('')
const category = ref('All')
const pending = ref(null)
const pendingBranch = ref('')
const loading = ref(false)
const error = ref('')

// Custom repo / branch form
const customRepo = ref('')
const customBranch = ref('')

function isFrappe(app) {
  return Boolean(app.repo?.includes('github.com/frappe/'))
}

const installableSet = computed(() => new Set(props.installable))
const installedSet = computed(() => new Set(props.installedApps))
const registryNames = computed(() => new Set(props.registry.map(a => a.name)))
const extraInstallable = computed(() => props.installable.filter(name => !registryNames.value.has(name)))

const sortedRegistry = computed(() =>
  [...props.registry].sort((a, b) => {
    const af = isFrappe(a), bf = isFrappe(b)
    if (af !== bf) return af ? -1 : 1
    return (a.title || a.name).localeCompare(b.title || b.name)
  })
)

const filteredRegistry = computed(() => {
  let apps = sortedRegistry.value
  if (category.value !== 'All') apps = apps.filter(a => a.category === category.value)
  const q = search.value.toLowerCase().trim()
  if (q) apps = apps.filter(a =>
    (a.title || a.name).toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q)
  )
  return apps
})

const branchOptions = computed(() =>
  (pending.value?.branches ?? []).map(b => ({ label: b, value: b }))
)

const dialogTitle = computed(() => {
  if (mode.value === 'confirm' && pending.value) return pending.value.title || pending.value.name
  if (mode.value === 'custom') return 'Install Custom App'
  return 'Install App'
})

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']
function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

// Reset to a clean browse view whenever the dialog opens.
function reset() {
  mode.value = 'browse'
  search.value = ''
  category.value = 'All'
  pending.value = null
  pendingBranch.value = ''
  customRepo.value = ''
  customBranch.value = ''
  error.value = ''
}

function onOpen(open) {
  if (open) reset()
}

function selectApp(app) {
  const branches = app.branches ?? (app.branch ? [app.branch] : [])
  pendingBranch.value = branches[0] ?? ''
  pending.value = app
  error.value = ''
  mode.value = 'confirm'
}

function openCustom() {
  customRepo.value = ''
  customBranch.value = ''
  error.value = ''
  mode.value = 'custom'
  loadGitStatus()
}

// ── Git provider integration (private repositories) ────────────────────────────
const gitStatus = ref(null)        // { connected, provider, is_token_valid, providers }
const gitProvider = ref('github')
const gitToken = ref('')
const gitConnecting = ref(false)
const gitError = ref('')
const showConnect = ref(false)     // reveal the token form even before connecting
const repos = ref([])
const reposLoading = ref(false)
const repoSearch = ref('')

const tokenHelpUrl = computed(() => gitStatus.value?.providers?.[gitProvider.value] || '')

const gitConnected = computed(() => gitStatus.value?.connected && gitStatus.value?.is_token_valid)
const gitPaused = computed(() => gitStatus.value?.connected && !gitStatus.value?.is_token_valid)

const filteredRepos = computed(() => {
  const q = repoSearch.value.toLowerCase().trim()
  if (!q) return repos.value
  return repos.value.filter(r => (r.full_name || '').toLowerCase().includes(q))
})

async function loadGitStatus() {
  gitError.value = ''
  try {
    const res = await fetch('/api/git/integration')
    gitStatus.value = await res.json()
    gitProvider.value = gitStatus.value?.provider || 'github'
    showConnect.value = false
    if (gitConnected.value) loadRepos()
    else repos.value = []
  } catch (e) {
    gitError.value = e.message
  }
}

async function loadRepos() {
  reposLoading.value = true
  gitError.value = ''
  try {
    const res = await fetch('/api/git/repos')
    const d = await res.json()
    if (d.ok) {
      repos.value = d.repos
    } else if (d.token_invalid) {
      // Self-healing: surface the re-auth panel without losing existing deploys.
      if (gitStatus.value) gitStatus.value.is_token_valid = false
      repos.value = []
    } else {
      gitError.value = d.error
    }
  } catch (e) {
    gitError.value = e.message
  } finally {
    reposLoading.value = false
  }
}

async function connectGit() {
  const token = gitToken.value.trim()
  if (!token) { gitError.value = 'Paste a personal access token to connect.'; return }
  gitConnecting.value = true
  gitError.value = ''
  try {
    const res = await fetch('/api/git/integration', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: gitProvider.value, token }),
    })
    const d = await res.json()
    if (d.ok) {
      gitToken.value = ''
      gitStatus.value = d.status
      showConnect.value = false
      loadRepos()
    } else {
      gitError.value = d.error
    }
  } catch (e) {
    gitError.value = e.message
  } finally {
    gitConnecting.value = false
  }
}

async function disconnectGit() {
  gitError.value = ''
  try {
    await fetch('/api/git/integration', { method: 'DELETE' })
    repos.value = []
    await loadGitStatus()
  } catch (e) {
    gitError.value = e.message
  }
}

function selectRepo(repo) {
  customRepo.value = repo.clone_url
  customBranch.value = repo.default_branch || ''
  repoSearch.value = repo.full_name
}

function backToBrowse() {
  pending.value = null
  error.value = ''
  mode.value = 'browse'
}

async function postInstall(url, body) {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const d = await res.json()
    if (d.ok) {
      show.value = false
      watchTask(d.task_id)
    } else {
      error.value = d.error
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function confirmInstall() {
  if (!pending.value) return
  const inBench = installableSet.value.has(pending.value.name)
  if (inBench) {
    postInstall(`/api/sites/${props.siteName}/install-app`, { app: pending.value.name })
  } else {
    postInstall(`/api/sites/${props.siteName}/get-and-install-app`, {
      app: pending.value.name,
      repo: pending.value.repo,
      branch: pendingBranch.value,
    })
  }
}

function installBenchApp(appName) {
  postInstall(`/api/sites/${props.siteName}/install-app`, { app: appName })
}

function confirmCustomInstall() {
  const repo = customRepo.value.trim()
  if (!repo) { error.value = 'Repository URL is required.'; return }
  postInstall(`/api/sites/${props.siteName}/get-and-install-app`, {
    repo,
    branch: customBranch.value.trim(),
  })
}
</script>

<template>
  <Dialog v-model="show" :options="{ title: dialogTitle, size: 'xl' }" @update:modelValue="onOpen">
    <template #body-content>
      <div @pointerdown.stop>
        <!-- Confirmation view -->
        <template v-if="mode === 'confirm' && pending">
          <div class="flex flex-col gap-4">
            <div class="flex items-center gap-3">
              <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg overflow-hidden"
                :style="pending.logo_url ? {} : { background: hashColor(pending.name) }">
                <img v-if="pending.logo_url" :src="pending.logo_url" class="h-full w-full object-contain" :alt="pending.title" />
                <span v-else class="text-sm font-bold text-white leading-none">{{ pending.title?.[0]?.toUpperCase() }}</span>
              </div>
              <div class="min-w-0">
                <p class="font-medium text-ink-gray-9">{{ pending.title || pending.name }}</p>
                <p v-if="pending.description" class="text-sm text-ink-gray-5 line-clamp-2">{{ pending.description }}</p>
              </div>
            </div>
            <FormControl v-if="branchOptions.length > 1" label="Branch" type="select"
              v-model="pendingBranch" :options="branchOptions" />
            <p v-else-if="pendingBranch" class="text-sm text-ink-gray-6">
              Branch: <span class="font-medium text-ink-gray-9">{{ pendingBranch }}</span>
            </p>
            <ErrorMessage v-if="error" :message="error" />
            <div class="flex items-center justify-between gap-2">
              <Button variant="ghost" @click="backToBrowse">← Back</Button>
              <div class="flex gap-2">
                <Button variant="ghost" @click="show = false">Cancel</Button>
                <Button variant="solid" :loading="loading" @click="confirmInstall">Install</Button>
              </div>
            </div>
          </div>
        </template>

        <!-- Custom repo / branch view -->
        <template v-else-if="mode === 'custom'">
          <div class="flex flex-col gap-4">
            <p class="text-sm text-ink-gray-5">
              Fetch an app from any git repository and install it on this site.
            </p>

            <!-- ── Git connection panel ─────────────────────────────────── -->
            <!-- Connection paused: token expired/revoked -->
            <div v-if="gitPaused"
              class="flex flex-col gap-2.5 rounded-lg border border-outline-amber-2 bg-surface-amber-1 p-3">
              <div class="flex items-start gap-2">
                <LucideTriangleAlert class="mt-0.5 h-4 w-4 shrink-0 text-ink-amber-3" />
                <div class="text-sm text-ink-gray-7">
                  <p class="font-medium text-ink-gray-8">Connection paused</p>
                  <p class="mt-0.5">Your {{ gitProvider }} token has expired or been revoked. Existing apps keep
                    working — but you can't browse new repositories until you refresh the connection.</p>
                </div>
              </div>
              <div class="flex items-end gap-2">
                <FormControl class="flex-1" label="New access token" type="password" v-model="gitToken"
                  placeholder="Paste a fresh token" @keydown.enter="connectGit" />
                <Button variant="solid" :loading="gitConnecting" @click="connectGit">Update Token</Button>
              </div>
            </div>

            <!-- Connected: repository picker -->
            <div v-else-if="gitConnected"
              class="flex flex-col gap-2.5 rounded-lg border border-outline-gray-2 p-3">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2 text-sm">
                  <span class="inline-block h-1.5 w-1.5 rounded-full bg-surface-green-3"></span>
                  <span class="font-medium text-ink-gray-8 capitalize">{{ gitProvider }}</span>
                  <span class="text-ink-gray-5">connected</span>
                </div>
                <Button variant="ghost" size="sm" @click="disconnectGit">Disconnect</Button>
              </div>
              <TextInput v-model="repoSearch" placeholder="Search your repositories…" />
              <div class="max-h-48 overflow-y-auto flex flex-col gap-1">
                <p v-if="reposLoading" class="py-4 text-center text-sm text-ink-gray-4">Loading repositories…</p>
                <p v-else-if="!filteredRepos.length" class="py-4 text-center text-sm text-ink-gray-4">No repositories found.</p>
                <button v-for="repo in filteredRepos" :key="repo.full_name" type="button"
                  @click="selectRepo(repo)"
                  :class="[
                    'flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-left transition-colors',
                    customRepo === repo.clone_url
                      ? 'border-outline-gray-4 bg-surface-gray-2'
                      : 'border-transparent hover:bg-surface-gray-2',
                  ]">
                  <div class="min-w-0 flex-1">
                    <p class="truncate text-sm font-medium text-ink-gray-8">{{ repo.full_name }}</p>
                    <p v-if="repo.description" class="truncate text-xs text-ink-gray-5">{{ repo.description }}</p>
                  </div>
                  <Badge v-if="repo.private" label="Private" theme="orange" size="sm" />
                </button>
              </div>
            </div>

            <!-- Not connected: connect prompt -->
            <div v-else class="flex flex-col gap-2.5 rounded-lg border border-dashed border-outline-gray-2 p-3">
              <template v-if="!showConnect">
                <div class="flex items-center justify-between gap-2">
                  <p class="text-sm text-ink-gray-6">Connect GitHub to browse and install private repositories.</p>
                  <Button variant="subtle" size="sm" @click="showConnect = true">Connect</Button>
                </div>
              </template>
              <template v-else>
                <FormControl label="Provider" type="select" v-model="gitProvider"
                  :options="[{ label: 'GitHub', value: 'github' }]" />
                <FormControl label="Personal access token" type="password" v-model="gitToken"
                  placeholder="ghp_…" @keydown.enter="connectGit" />
                <a v-if="tokenHelpUrl" :href="tokenHelpUrl" target="_blank" rel="noopener"
                  class="text-xs text-ink-blue-3 hover:underline">Generate a token →</a>
                <div class="flex justify-end gap-2">
                  <Button variant="ghost" size="sm" @click="showConnect = false">Cancel</Button>
                  <Button variant="solid" size="sm" :loading="gitConnecting" @click="connectGit">Connect</Button>
                </div>
              </template>
            </div>

            <ErrorMessage v-if="gitError" :message="gitError" />

            <!-- ── Manual repo + branch ─────────────────────────────────── -->
            <FormControl label="Repository URL" type="text" v-model="customRepo"
              placeholder="https://github.com/owner/app" />
            <FormControl label="Branch" type="text" v-model="customBranch"
              placeholder="Leave blank for the repository default" />
            <ErrorMessage v-if="error" :message="error" />
            <div class="flex items-center justify-between gap-2">
              <Button variant="ghost" @click="backToBrowse">← Back</Button>
              <div class="flex gap-2">
                <Button variant="ghost" @click="show = false">Cancel</Button>
                <Button variant="solid" :loading="loading" :disabled="!customRepo.trim()"
                  @click="confirmCustomInstall">Install</Button>
              </div>
            </div>
          </div>
        </template>

        <!-- Browse view -->
        <template v-else>
          <div class="flex flex-col gap-3">
            <TextInput v-model="search" placeholder="Search apps…" />
            <div class="flex gap-1.5 overflow-x-auto pb-1">
              <button v-for="cat in INSTALL_CATEGORIES" :key="cat"
                @click="category = cat"
                :class="[
                  'shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition-colors whitespace-nowrap',
                  category === cat
                    ? 'border-outline-gray-4 bg-surface-gray-3 text-ink-gray-9'
                    : 'border-outline-gray-2 bg-surface-white text-ink-gray-6 hover:border-outline-gray-3',
                ]">{{ cat }}</button>
            </div>
            <div class="max-h-80 overflow-y-auto flex flex-col gap-2 pr-1">
              <p v-if="!registry.length" class="py-8 text-center text-sm text-ink-gray-4">Loading apps…</p>
              <p v-else-if="!filteredRegistry.length && !extraInstallable.length" class="py-8 text-center text-sm text-ink-gray-4">No apps found.</p>
              <template v-else>
                <div v-for="app in filteredRegistry" :key="app.name"
                  class="flex items-center gap-3 rounded-lg border border-outline-gray-1 px-3 py-2.5">
                  <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md overflow-hidden"
                    :style="app.logo_url ? {} : { background: hashColor(app.name) }">
                    <img v-if="app.logo_url" :src="app.logo_url" :alt="app.title" class="h-full w-full object-contain" />
                    <span v-else class="text-xs font-bold text-white leading-none">{{ app.title?.[0]?.toUpperCase() }}</span>
                  </div>
                  <div class="flex-1 min-w-0">
                    <p class="text-sm font-medium text-ink-gray-9">{{ app.title || app.name }}</p>
                    <p v-if="app.description" class="text-xs text-ink-gray-5 truncate">{{ app.description }}</p>
                  </div>
                  <div class="shrink-0">
                    <Badge v-if="installedSet.has(app.name)" label="Installed" theme="green" size="sm" />
                    <Button v-else-if="app.repo" variant="outline" size="sm" @click="selectApp(app)">Add</Button>
                  </div>
                </div>
                <template v-if="!search && category === 'All' && extraInstallable.length">
                  <p class="mt-2 text-xs font-medium uppercase tracking-wide text-ink-gray-4">Other (in bench)</p>
                  <div v-for="appName in extraInstallable" :key="appName"
                    class="flex items-center gap-3 rounded-lg border border-outline-gray-1 px-3 py-2.5">
                    <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md overflow-hidden"
                      :style="{ background: hashColor(appName) }">
                      <span class="text-xs font-bold text-white leading-none">{{ appName[0].toUpperCase() }}</span>
                    </div>
                    <div class="flex-1 min-w-0">
                      <p class="text-sm font-medium text-ink-gray-9">{{ appName }}</p>
                    </div>
                    <Button variant="outline" size="sm" :loading="loading" @click="installBenchApp(appName)">Add</Button>
                  </div>
                </template>
              </template>
            </div>
            <ErrorMessage v-if="error" :message="error" />
            <!-- Custom repository entry point -->
            <button type="button" @click="openCustom"
              class="flex items-center justify-center gap-2 rounded-lg border border-dashed border-outline-gray-2 px-3 py-2.5 text-sm font-medium text-ink-gray-6 transition-colors hover:border-outline-gray-3 hover:text-ink-gray-8">
              Install a custom app
            </button>
          </div>
        </template>
      </div>
    </template>
  </Dialog>
</template>
