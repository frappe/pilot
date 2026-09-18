<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
  Alert,
  Button,
  Combobox,
  Dialog,
  ErrorMessage,
  LoadingText,
  Spinner,
  TabButtons,
  TextInput,
} from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { appsApi } from '@/api/apps'
import { gitApi } from '@/api/git'
import { branchComboboxOptions } from '@/utils/branchComboboxOptions'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  // When set, the fetched app is also installed on this site.
  siteName?: string
}

const props = withDefaults(defineProps<Props>(), {
  siteName: '',
})
const open = defineModel('open')
const router = useRouter()

const tab = ref('public')
const tabOptions = [
  { label: 'Public repository', value: 'public', class: 'flex-1' },
  { label: 'Your GitHub account', value: 'private', class: 'flex-1' },
]
const repo = ref('')
const branch = ref('')
const fetched = ref(false)
const fetching = ref(false)
const branches = ref([])
const branchOptions = computed(() => branches.value.map((b) => ({ label: b, value: b })))
const manualBranchOptions = computed(() =>
  branchComboboxOptions(branches.value, branch.value, (typed) => {
    branch.value = typed
  }),
)

const gitStatus = ref(null)
const gitConnected = computed(() =>
  Boolean(gitStatus.value?.connected && gitStatus.value?.is_token_valid),
)
const repos = ref([])
const reposLoading = ref(false)
const repoOptions = computed(() =>
  repos.value.map((r) => ({ label: r.full_name, value: r.clone_url })),
)

const adding = ref(false)
const error = ref('')

const needsGithubConnection = computed(
  () => tab.value === 'private' && Boolean(gitStatus.value) && !gitConnected.value,
)

const goToGithubSettings = () => {
  open.value = false
  router.push({ name: 'Settings', params: { section: 'general', subSection: 'github' } })
}

const resolving = ref(false)
const foundName = ref('')
const canSubmit = computed(() =>
  Boolean(repo.value.trim() && branch.value.trim() && foundName.value && !resolving.value),
)

watch(open, (isOpen) => {
  if (isOpen) reset()
})
watch(tab, () => reset())
// Accepts scheme-less URLs; every API call goes through normalizedRepo.
const normalizedRepo = computed(() => {
  const url = repo.value.trim().replace(/\/+$/, '')
  if (!url) return ''
  return /^https?:\/\//i.test(url) ? url : `https://${url}`
})

// host/owner/repo, with or without a scheme.
const REPO_PATTERN = /^(https?:\/\/)?[^/\s]+\.[^/\s]+\/[^/\s]+\/[^/\s]+$/

// Auto-fetch once the URL looks complete.
let repoDebounce
watch(repo, (value) => {
  fetched.value = false
  branches.value = []
  foundName.value = ''
  if (tab.value !== 'public') return
  clearTimeout(repoDebounce)
  if (!REPO_PATTERN.test(value.trim().replace(/\/+$/, ''))) return
  const url = normalizedRepo.value
  repoDebounce = setTimeout(() => loadBranchesFor(url), 600)
})

const reset = () => {
  clearTimeout(repoDebounce)
  repo.value = ''
  branch.value = ''
  fetched.value = false
  branches.value = []
  foundName.value = ''
  error.value = ''
  if (tab.value === 'private' && !gitStatus.value) loadGitStatus()
}

const loadBranchesFor = async (url) => {
  fetching.value = true
  error.value = ''
  try {
    const d = await gitApi.branches(url)
    if (d.branches) {
      branches.value = d.branches
      branch.value = d.branches[0] || ''
      fetched.value = true
    } else {
      error.value = apiErrorMessage(d, 'Could not load branches.')
    }
  } catch (e) {
    error.value = e.message
  } finally {
    fetching.value = false
  }
}

const loadGitStatus = async () => {
  gitStatus.value = await gitApi.status()
  if (gitConnected.value) {
    reposLoading.value = true
    try {
      const d = await gitApi.repos()
      if (Array.isArray(d)) repos.value = d
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

const resolveApp = async () => {
  resolving.value = true
  foundName.value = ''
  error.value = ''
  try {
    const d = await gitApi.resolve(normalizedRepo.value, branch.value.trim())
    if (d.name) foundName.value = d.name
    else error.value = apiErrorMessage(d, 'Could not find a Frappe app in this repository.')
  } catch (e) {
    error.value = e.message
  } finally {
    resolving.value = false
  }
}

const submit = async () => {
  if (!canSubmit.value || adding.value) return
  adding.value = true
  error.value = ''
  try {
    const result = await appsApi.add({
      name: foundName.value,
      repo: normalizedRepo.value,
      branch: branch.value.trim(),
      sites: props.siteName ? [props.siteName] : [],
    })
    if (!result.task_id) throw new Error(apiErrorMessage(result, 'Could not import app.'))
    open.value = false
    openTaskDetailPage(router, result.task_id)
  } catch (caught) {
    error.value = caught.message || 'Could not import app.'
  } finally {
    adding.value = false
  }
}
</script>

<template>
  <Dialog v-model="open" title="Import app from GitHub" size="md">
    <TabButtons
      v-model="tab"
      :options="tabOptions"
      size="md"
      class="mb-4 w-full [&>div]:w-full [&_[data-slot=tab-button]>span]:w-full"
    />

    <div v-if="tab === 'public'" class="flex items-end gap-2">
      <TextInput
        label="Repository URL"
        v-model="repo"
        class="flex-1"
        placeholder="https://github.com/frappe/crm"
      />
      <Combobox
        v-if="fetched"
        label="Branch"
        v-model="branch"
        :options="manualBranchOptions"
        :loading="fetching"
        trigger="button"
        placeholder="Search or type a branch…"
        emptyText="No matching branch. Type one to use it."
        class="w-40 shrink-0"
      >
        <template #item-typed-branch="{ query }">
          Use branch “{{ query }}”
        </template>
      </Combobox>
    </div>

    <template v-else>
      <p v-if="!gitStatus" class="text-ink-gray-5 text-sm">Loading…</p>

      <Alert
        v-else-if="!gitConnected"
        class="border border-outline-gray-2"
        theme="amber"
        title="No GitHub account connected"
        :dismissible="false"
      />

      <template v-else>
        <p
          class="bg-surface-gray-1 px-3 py-2 border rounded-6 border-outline-gray-2 text-ink-gray-7 text-sm"
        >
          Connected as
          <span class="font-medium text-ink-gray-9">{{ gitStatus.username }}</span>
        </p>

        <LoadingText v-if="reposLoading" class="justify-center h-32" />

        <div v-else class="flex items-end gap-2 mt-2">
          <Combobox
            label="Repository"
            v-model="repo"
            :options="repoOptions"
            class="flex-1"
            placeholder="Search repositories…"
            emptyText="No repositories found."
          />
          <Combobox
            v-if="fetched"
            label="Branch"
            v-model="branch"
            :options="branchOptions"
            :loading="fetching"
            placeholder="Search branches…"
            class="w-40 shrink-0"
          />
        </div>
      </template>
    </template>

    <ErrorMessage v-if="error" :message="error" class="mt-1.5" />

    <p
      v-else
      class="flex items-center gap-1.5 mt-1.5 h-5 text-sm"
      :class="foundName ? 'text-ink-green-7' : 'text-ink-gray-5'"
    >
      <Spinner v-if="fetching || resolving" />
      <span v-else-if="foundName" class="size-3.5 shrink-0 lucide-check" />

      <template v-if="fetching">Loading branches…</template>
      <template v-else-if="resolving">Checking repository…</template>
      <template v-else-if="foundName"
        >Found {{ foundName
        }}<template v-if="siteName">, will be installed on {{ siteName }}</template></template
      >
    </p>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button @click="open = false">Cancel</Button>
        <Button v-if="needsGithubConnection" variant="solid" @click="goToGithubSettings"
          >Connect GitHub</Button
        >
        <Button v-else variant="solid" :disabled="!canSubmit" :loading="adding" @click="submit">
          {{ siteName ? 'Import and install' : 'Import app' }}
        </Button>
      </div>
    </template>
  </Dialog>
</template>
