<script setup>
import { ref, computed } from 'vue'
import { Button, Badge, Combobox, Dialog, FormControl, TextInput, ErrorMessage } from 'frappe-ui'
import { useTaskProgress } from '../composables/useTaskProgress.js'
import { hashColor } from '../composables/useAppRegistry.js'
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
const loading = ref(false)
const error = ref('')

// Custom repo / branch form
const customRepo = ref('')
const customBranch = ref('')
const customTab = ref('public')  // 'public' | 'private'
const selectedRepo = ref(null)   // repo object chosen from GitHub picker, null = manual entry

function isFrappe(app) {
  return Boolean(app.repo?.includes('github.com/frappe/'))
}

const installableSet = computed(() => new Set(props.installable))
const installedSet = computed(() => new Set(props.installedApps))
const registryNames = computed(() => new Set(props.registry.map(a => a.name)))
const extraInstallable = computed(() => props.installable.filter(name => !registryNames.value.has(name)))

const sortedRegistry = computed(() =>
  [...props.registry].sort((a, b) => {
    if (a.is_installable !== b.is_installable) return a.is_installable ? -1 : 1
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

const dialogTitle = computed(() => {
  if (mode.value === 'confirm' && pending.value) return pending.value.title || pending.value.name
  if (mode.value === 'custom') return 'Install Custom App'
  return 'Install App'
})


// Reset to a clean browse view whenever the dialog opens.
function reset() {
  mode.value = 'browse'
  search.value = ''
  category.value = 'All'
  pending.value = null
  customRepo.value = ''
  customBranch.value = ''
  customTab.value = 'public'
  selectedRepo.value = null
  repoBranches.value = []
  error.value = ''
}

function onOpen(open) {
  if (open) reset()
}

function selectApp(app) {
  pending.value = app
  error.value = ''
  mode.value = 'confirm'
}

function openCustom() {
  customRepo.value = ''
  customBranch.value = ''
  selectedRepo.value = null
  repoBranches.value = []
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
const showConnect = ref(false)
const repos = ref([])
const reposLoading = ref(false)
const repoBranches = ref([])
const repoBranchesLoading = ref(false)
const repoComboboxOpen = ref(false)
const branchComboboxOpen = ref(false)

const repoOptions = computed(() =>
  repos.value.map(r => {
    const desc = (r.description || '').trim()
    return {
      label: r.full_name,
      value: r.clone_url,
      // Truncate at 72 chars so the dropdown never inflates beyond the trigger width.
      // The CSS max-width rule below is the final guard; the JS truncation reduces
      // intrinsic content width so the CSS variable (set asynchronously by floating-ui)
      // doesn't need to race against first-paint.
      description: desc.length > 72 ? desc.slice(0, 72) + '…' : desc,
    }
  })
)
const repoByCloneUrl = computed(() => new Map(repos.value.map(r => [r.clone_url, r])))

const tokenHelpUrl = computed(() =>
  gitStatus.value?.providers?.[gitProvider.value] ||
  (gitProvider.value === 'github' ? 'https://github.com/settings/tokens/new?scopes=repo&description=Bench+CLI' : '')
)

const gitConnected = computed(() => gitStatus.value?.connected && gitStatus.value?.is_token_valid)
const gitPaused = computed(() => gitStatus.value?.connected && !gitStatus.value?.is_token_valid)

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
  selectedRepo.value = repo
  customRepo.value = repo.clone_url
  customBranch.value = repo.default_branch || ''
  fetchRepoBranches(repo.full_name)
}

async function fetchRepoBranches(fullName) {
  repoBranchesLoading.value = true
  repoBranches.value = []
  try {
    const res = await fetch(`/api/git/branches?repo=${encodeURIComponent(fullName)}`)
    const d = await res.json()
    if (d.ok) repoBranches.value = d.branches
  } catch { /* branch list is optional — Combobox allowCustomValue handles manual entry */ }
  finally { repoBranchesLoading.value = false }
}

function onRepoSelect(option) {
  // frappe-ui emits null for BOTH "custom value confirmed" and "selection cleared".
  // Distinguish by checking customRepo, which v-model updates synchronously before
  // this handler runs.
  if (!option) {
    selectedRepo.value = null
    repoBranches.value = []
    if (!customRepo.value?.trim()) customBranch.value = ''  // only clear on true clear
    return
  }
  // Known repo from the list
  const repo = repoByCloneUrl.value.get(option.value)
  if (repo) selectRepo(repo)
}

function switchTab(tab) {
  customTab.value = tab
  error.value = ''
  gitError.value = ''
  if (tab === 'private' && !gitStatus.value) loadGitStatus()
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
      target: pending.value.target,
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
            <div v-if="pending.target" class="flex gap-4 text-sm text-ink-gray-6">
              <span>Version: <span class="font-medium text-ink-gray-9">{{ pending.version }}</span></span>
              <span>{{ pending.target_type }}: <span class="font-medium text-ink-gray-9">{{ pending.target }}</span></span>
            </div>
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
            <!-- Segment control -->
            <div class="grid grid-cols-2 gap-0.5 rounded-lg border border-outline-gray-2 bg-surface-gray-1 p-0.5">
              <button type="button"
                class="rounded-md px-3 py-1.5 text-sm font-medium transition-colors"
                :class="customTab === 'public'
                  ? 'bg-surface-white text-ink-gray-9 shadow-sm'
                  : 'text-ink-gray-5 hover:text-ink-gray-7'"
                @click="switchTab('public')">
                Public Repository
              </button>
              <button type="button"
                class="flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors"
                :class="customTab === 'private'
                  ? 'bg-surface-white text-ink-gray-9 shadow-sm'
                  : 'text-ink-gray-5 hover:text-ink-gray-7'"
                @click="switchTab('private')">
                <span v-if="gitConnected" class="h-1.5 w-1.5 rounded-full bg-surface-green-3"></span>
                {{ gitConnected ? 'Connected Account' : 'Connect Account' }}
              </button>
            </div>

            <!-- Public tab: URL + branch -->
            <template v-if="customTab === 'public'">
              <FormControl label="Repository URL" type="text" v-model="customRepo"
                placeholder="https://github.com/frappe/crm" />
              <FormControl label="Branch" type="text" v-model="customBranch"
                placeholder="Leave blank for the repository default" />
              <ErrorMessage v-if="error" :message="error" />
              <div class="flex items-center justify-between">
                <Button variant="ghost" @click="backToBrowse">← Back</Button>
                <div class="flex gap-2">
                  <Button variant="ghost" @click="show = false">Cancel</Button>
                  <Button variant="solid" :loading="loading" :disabled="!customRepo.trim()"
                    @click="confirmCustomInstall">Install</Button>
                </div>
              </div>
            </template>

            <!-- Private tab -->
            <template v-else>
              <!-- Loading -->
              <p v-if="!gitStatus && !gitError" class="py-4 text-center text-sm text-ink-gray-4">Loading…</p>

              <!-- Token paused (expired / revoked) -->
              <template v-else-if="gitPaused">
                <div class="flex items-start gap-2 rounded-lg border border-outline-amber-2 bg-surface-amber-1 p-3">
                  <LucideTriangleAlert class="mt-0.5 h-4 w-4 shrink-0 text-ink-amber-3" />
                  <div>
                    <p class="text-sm font-medium text-ink-gray-8">Connection paused</p>
                    <p class="mt-0.5 text-sm text-ink-gray-6">Your {{ gitProvider }} token has expired or been revoked.
                      Existing apps keep working — refresh the connection to browse new repositories.</p>
                  </div>
                </div>
                <FormControl label="New access token" type="text" v-model="gitToken"
                  placeholder="Paste a fresh token" @keydown.enter="connectGit" />
                <a v-if="tokenHelpUrl" :href="tokenHelpUrl" target="_blank" rel="noopener"
                  class="text-xs text-ink-blue-3 hover:underline">Generate a token →</a>
                <ErrorMessage v-if="gitError" :message="gitError" />
                <div class="flex items-center justify-between">
                  <Button variant="ghost" @click="backToBrowse">← Back</Button>
                  <div class="flex gap-2">
                    <Button variant="ghost" @click="show = false">Cancel</Button>
                    <Button variant="solid" :loading="gitConnecting" @click="connectGit">Update Token</Button>
                  </div>
                </div>
              </template>

              <!-- Connected: repo + branch comboboxes -->
              <template v-else-if="gitConnected">
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-1.5">
                    <span class="h-1.5 w-1.5 rounded-full bg-surface-green-3"></span>
                    <span class="text-sm font-medium capitalize text-ink-gray-8">{{ gitProvider }}</span>
                    <span class="text-sm text-ink-gray-5">connected</span>
                  </div>
                  <Button variant="ghost" size="sm" @click="disconnectGit">Disconnect</Button>
                </div>

                <div :class="((repoComboboxOpen && !reposLoading) || branchComboboxOpen) && 'pb-60'">
                  <div class="flex flex-col gap-4">
                    <Combobox
                      label="Repository"
                      v-model="customRepo"
                      :options="repoOptions"
                      :loading="reposLoading"
                      :allowCustomValue="true"
                      placeholder="Search or paste a URL…"
                      emptyText="No repositories found."
                      @update:selectedOption="onRepoSelect"
                      @update:open="repoComboboxOpen = $event"
                    >
                      <template #item-suffix="{ item }">
                        <Badge v-if="repoByCloneUrl.get(item.value)?.private" label="Private" theme="orange" size="sm" />
                      </template>
                    </Combobox>

                    <!-- Branch: shown as soon as a repo URL is committed (picker or paste) -->
                    <Combobox
                      v-if="customRepo && customRepo.trim()"
                      label="Branch"
                      v-model="customBranch"
                      :options="repoBranches.map(b => ({ label: b, value: b }))"
                      :loading="repoBranchesLoading"
                      :allowCustomValue="true"
                      placeholder="Leave blank for the default branch"
                      emptyText="No branches found."
                      @update:open="branchComboboxOpen = $event"
                    />
                  </div>
                </div>

                <ErrorMessage v-if="error || gitError" :message="error || gitError" />
                <div class="flex items-center justify-between">
                  <Button variant="ghost" @click="backToBrowse">← Back</Button>
                  <div class="flex gap-2">
                    <Button variant="ghost" @click="show = false">Cancel</Button>
                    <Button variant="solid" :loading="loading" :disabled="!customRepo.trim()"
                      @click="confirmCustomInstall">Install</Button>
                  </div>
                </div>
              </template>

              <!-- Not connected: token entry form -->
              <template v-else>
                <FormControl label="Provider" type="select" v-model="gitProvider"
                  :options="[{ label: 'GitHub', value: 'github' }]" />
                <FormControl label="Personal Access Token" type="text" v-model="gitToken"
                  placeholder="ghp_…" @keydown.enter="connectGit" />
                <a v-if="tokenHelpUrl" :href="tokenHelpUrl" target="_blank" rel="noopener"
                  class="text-xs text-ink-blue-3 hover:underline">Generate a token →</a>
                <ErrorMessage v-if="gitError" :message="gitError" />
                <div class="flex items-center justify-between">
                  <Button variant="ghost" @click="backToBrowse">← Back</Button>
                  <div class="flex gap-2">
                    <Button variant="ghost" @click="show = false">Cancel</Button>
                    <Button variant="solid" :loading="gitConnecting" @click="connectGit">Connect</Button>
                  </div>
                </div>
              </template>
            </template>
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
                  class="flex items-center gap-3 rounded-lg border px-3 py-2.5"
                  :class="app.is_installable
                    ? 'border-outline-gray-1'
                    : 'border-outline-gray-1 bg-surface-gray-1 opacity-60'">
                  <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md overflow-hidden"
                    :style="app.logo_url ? {} : { background: hashColor(app.name) }">
                    <img v-if="app.logo_url" :src="app.logo_url" :alt="app.title" class="h-full w-full object-contain" />
                    <span v-else class="text-xs font-bold text-white leading-none">{{ app.title?.[0]?.toUpperCase() }}</span>
                  </div>
                  <div class="flex-1 min-w-0">
                    <p class="text-sm font-medium text-ink-gray-9">{{ app.title || app.name }}</p>
                    <p v-if="app.description" class="text-xs text-ink-gray-5 truncate">{{ app.description }}</p>
                    <p v-if="!app.is_installable && app.required_version" class="text-xs text-ink-gray-4">
                      Requires Frappe {{ app.required_version }}
                    </p>
                  </div>
                  <div class="shrink-0">
                    <Badge v-if="installedSet.has(app.name)" label="Installed" theme="green" size="sm" />
                    <Button v-else-if="app.is_installable && app.repo" variant="outline" size="sm" @click="selectApp(app)">Add</Button>
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

<!--
  The Combobox portals its dropdown to <body> (position: fixed, floating-ui).
  floating-ui sets --reka-combobox-trigger-width to the anchor's measured width,
  and frappe-ui's ComboboxContent applies min-w-[--reka-combobox-trigger-width].
  But there is no max-w, so long option descriptions inflate the intrinsic size
  and push the dropdown past the trigger width. Cap it here.
  Non-scoped so the rule reaches the portaled element outside the component root.
-->
<style>
[data-slot='content'][data-selection] {
  max-width: var(--reka-combobox-trigger-width, 600px);
}
</style>
