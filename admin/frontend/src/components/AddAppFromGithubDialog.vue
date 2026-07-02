<template>
  <Dialog v-model="open" title="Add app from GitHub" size="lg">
    <template #default>
      <div class="space-y-5">
        <TabButtons v-model="tab" :options="tabOptions" type="underline" size="md" />

        <template v-if="tab === 'public'">
          <div class="flex items-end gap-2">
            <FormControl label="Repository URL" type="text" v-model="repo" class="flex-1"
              placeholder="https://github.com/frappe/crm" />
            <Combobox v-if="fetched" label="Branch" v-model="branch" :options="branchOptions"
              placeholder="Search branches…" class="w-40 shrink-0" />
            <Button v-else variant="subtle" class="shrink-0" :loading="fetching" :disabled="!repo.trim()"
              @click="fetchBranches">
              Fetch branches
            </Button>
          </div>
        </template>

        <template v-else>
          <p v-if="!gitStatus" class="text-ink-gray-5 text-sm">Loading…</p>
          <Alert v-else-if="!gitConnected" theme="yellow" title="No GitHub account connected" :dismissible="false">
            <template #description>
              <p class="text-ink-gray-6 text-p-sm">
                Connect a personal access token from Settings → GitHub to browse your repositories.
              </p>
            </template>
          </Alert>
          <template v-else>
            <div class="flex items-center gap-2 bg-surface-gray-1 px-3 py-2 border rounded-lg border-outline-gray-2">
              <span class="size-3.5 text-ink-green-4 lucide-circle-check shrink-0"></span>
              <span class="text-ink-gray-7 text-p-sm">
                Connected as <span class="font-medium text-ink-gray-9">{{ gitStatus.username }}</span>
              </span>
            </div>
            <div v-if="reposLoading" class="flex justify-center items-center h-32">
              <LoadingText />
            </div>
            <div v-else class="flex items-end gap-2">
              <Combobox label="Repository" v-model="repo" :options="repoOptions" class="flex-1"
                placeholder="Search repositories…" emptyText="No repositories found." />
              <Combobox v-if="fetched" label="Branch" v-model="branch" :options="branchOptions" :loading="fetching"
                placeholder="Search branches…" class="w-40 shrink-0" />
            </div>
          </template>
        </template>

        <p v-if="resolving" class="text-ink-gray-5 text-sm">Checking repository…</p>
        <p v-else-if="foundName" class="flex items-center gap-1.5 text-ink-green-4 text-sm">
          <span class="size-4 lucide-circle-check"></span>
          Found {{ foundName }}
        </p>

        <ErrorMessage v-if="error" :message="error" />

        <div class="flex justify-end gap-2">
          <Button variant="subtle" @click="open = false">Cancel</Button>
          <Button variant="solid" :disabled="!canSubmit" :loading="adding" @click="submit">Add App</Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Alert, Button, Combobox, Dialog, ErrorMessage, FormControl, LoadingText, TabButtons } from 'frappe-ui'
import { appsApi } from '@/api/apps'
import { gitApi } from '@/api/git'
import { openTaskDetailPage } from '@/utils/taskRoute'

const open = defineModel('open')
const router = useRouter()

const tab = ref('public')
const tabOptions = [
  { label: 'Public repository', value: 'public' },
  { label: 'Your GitHub account', value: 'private' },
]
const repo = ref('')
const branch = ref('')
const fetched = ref(false)
const fetching = ref(false)
const branches = ref([])
const branchOptions = computed(() => branches.value.map((b) => ({ label: b, value: b })))

const gitStatus = ref(null)
const gitConnected = computed(() => Boolean(gitStatus.value?.connected && gitStatus.value?.is_token_valid))
const repos = ref([])
const reposLoading = ref(false)
const repoOptions = computed(() => repos.value.map((r) => ({ label: r.full_name, value: r.clone_url })))

const adding = ref(false)
const error = ref('')

const resolving = ref(false)
const foundName = ref('')
const canSubmit = computed(() => Boolean(repo.value.trim() && branch.value.trim() && foundName.value && !resolving.value))

watch(open, (isOpen) => {
  if (isOpen) reset()
})
watch(tab, reset)
watch(repo, () => {
  fetched.value = false
  branches.value = []
  foundName.value = ''
})

function reset() {
  repo.value = ''
  branch.value = ''
  fetched.value = false
  branches.value = []
  foundName.value = ''
  error.value = ''
  if (tab.value === 'private' && !gitStatus.value) loadGitStatus()
}

async function loadBranchesFor(url) {
  fetching.value = true
  error.value = ''
  try {
    const d = await gitApi.branches(url)
    if (d.ok) {
      branches.value = d.branches
      branch.value = d.branches[0] || ''
      fetched.value = true
    } else {
      error.value = d.error
    }
  } catch (e) {
    error.value = e.message
  } finally {
    fetching.value = false
  }
}

function fetchBranches() {
  const url = repo.value.trim()
  if (url) loadBranchesFor(url)
}

async function loadGitStatus() {
  gitStatus.value = await gitApi.status()
  if (gitConnected.value) {
    reposLoading.value = true
    try {
      const d = await gitApi.repos()
      if (d.ok) repos.value = d.repos
    } finally {
      reposLoading.value = false
    }
  }
}

watch(
  () => (tab.value === 'private' ? repo.value : null),
  (cloneUrl) => {
    if (cloneUrl) loadBranchesFor(cloneUrl)
  },
)

watch(branch, () => {
  if (repo.value.trim() && branch.value.trim()) resolveApp()
})

async function resolveApp() {
  resolving.value = true
  foundName.value = ''
  error.value = ''
  try {
    const d = await gitApi.resolve(repo.value.trim(), branch.value.trim())
    if (d.ok) foundName.value = d.name
    else error.value = d.error || 'Could not find a Frappe app in this repository.'
  } catch (e) {
    error.value = e.message
  } finally {
    resolving.value = false
  }
}

async function submit() {
  if (!canSubmit.value || adding.value) return
  adding.value = true
  error.value = ''
  try {
    const result = await appsApi.add({ name: foundName.value, repo: repo.value.trim(), branch: branch.value.trim() })
    if (!result.ok) throw new Error(result.error || 'Could not add app.')
    open.value = false
    openTaskDetailPage(router, result.task_id)
  } catch (caught) {
    error.value = caught.message || 'Could not add app.'
  } finally {
    adding.value = false
  }
}
</script>
