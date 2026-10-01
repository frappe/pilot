<script setup lang="ts">
import { Button, Checkbox, Dialog, ErrorMessage, Select } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { appsApi } from '@/api/apps'
import { gitApi } from '@/api/git'
import { apiErrorMessage } from '@/api/client'
import { errorMessage } from '@/utils/error'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface AppBranchTarget {
  name: string
  title: string
  repo: string
  branch: string
}

interface Props {
  app: AppBranchTarget | null
}

const props = defineProps<Props>()
const open = defineModel<boolean>('open')
const router = useRouter()

const branch = ref('')
const branches = ref<string[]>([])
const force = ref(false)
const loadingBranches = ref(false)
const submitting = ref(false)
const error = ref('')
const loadedFor = ref('')
let branchRequest = 0

const branchOptions = computed(() => branches.value.map((value) => ({ label: value, value })))
const currentTarget = computed(() => (props.app ? `${props.app.name}\n${props.app.repo}` : ''))
const canSubmit = computed(
  () =>
    Boolean(props.app) &&
    !loadingBranches.value &&
    loadedFor.value === currentTarget.value &&
    branches.value.includes(branch.value),
)

const loadBranches = async () => {
  const app = props.app
  if (!app?.repo) return
  const requestId = ++branchRequest
  const target = `${app.name}\n${app.repo}`
  loadingBranches.value = true
  error.value = ''
  loadedFor.value = ''
  branches.value = []
  branch.value = ''
  try {
    const result = await gitApi.branches(app.repo)
    if (requestId !== branchRequest || target !== currentTarget.value || !open.value) return
    branches.value = result.branches || []
    loadedFor.value = target
    branch.value =
      (branches.value.includes(app.branch) && app.branch) ||
      (branches.value.includes(result.default_branch) && result.default_branch) ||
      branches.value[0] ||
      ''
  } catch (caught) {
    if (requestId !== branchRequest || target !== currentTarget.value || !open.value) return
    error.value = errorMessage(caught, 'Could not load repository branches.')
  } finally {
    if (requestId === branchRequest) loadingBranches.value = false
  }
}

watch(
  () => [open.value, props.app] as const,
  ([isOpen]) => {
    branchRequest += 1
    force.value = false
    branches.value = []
    branch.value = ''
    loadedFor.value = ''
    error.value = ''
    if (isOpen) loadBranches()
  },
)

const submit = async () => {
  if (!props.app || !canSubmit.value || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const result = await appsApi.switchBranch(props.app.name, branch.value, force.value)
    if (!result.task_id) throw new Error(apiErrorMessage(result, 'Could not start branch switch.'))
    open.value = false
    openTaskDetailPage(router, result.task_id)
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not switch branch.')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Dialog v-model="open" title="Change app branch" size="sm">
    <form class="flex flex-col gap-3" @submit.prevent="submit">
      <div v-if="app" class="text-p-sm text-ink-gray-6">
        <p class="font-medium text-ink-gray-8">{{ app.title }}</p>
        <p class="mt-0.5">Current branch: <span class="font-mono">{{ app.branch || 'Unknown' }}</span></p>
      </div>

      <Select
        v-model="branch"
        label="Target branch"
        :options="branchOptions"
        :loading="loadingBranches"
        :disabled="loadingBranches || !branchOptions.length"
      />

      <Checkbox
        v-model="force"
        label="Force switch and discard local tracked/untracked changes"
      />

      <p v-if="force" class="text-p-sm text-ink-red-3">
        Force switch permanently discards local working-tree changes before switching.
      </p>

      <ErrorMessage v-if="error" :message="error" />

      <div class="flex justify-end gap-2 mt-2">
        <Button @click="open = false">Cancel</Button>
        <Button type="submit" variant="solid" :loading="submitting" :disabled="!canSubmit">
          Switch branch
        </Button>
      </div>
    </form>
  </Dialog>
</template>
