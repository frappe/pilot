<script setup lang="ts">
import { Select } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { appsApi } from '@/api/apps'
import { apiErrorMessage } from '@/api/client'
import { gitApi } from '@/api/git'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { errorMessage } from '@/utils/error'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  app: { name: string; title: string; logo_url: string | null; branch: string; repo: string } | null
}

const props = defineProps<Props>()
const open = defineModel<boolean>('open', { default: false })
const router = useRouter()

const branches = ref<string[]>([])
const branch = ref('')
const loadingBranches = ref(false)
const switching = ref(false)
const error = ref('')

const options = computed(() =>
  branches.value
    .filter((name) => name !== props.app?.branch)
    .map((name) => ({ label: name, value: name })),
)

const loadBranches = async () => {
  if (!props.app?.repo) return
  branch.value = ''
  branches.value = []
  error.value = ''
  loadingBranches.value = true
  try {
    const data = await gitApi.branches(props.app.repo)
    if ('branches' in data) branches.value = data.branches
    else error.value = apiErrorMessage(data, 'Could not list the branches of this repository.')
  } catch (e) {
    error.value = errorMessage(e, 'Could not list the branches of this repository.')
  } finally {
    loadingBranches.value = false
  }
}

watch(open, (isOpen) => isOpen && loadBranches())

const confirm = async () => {
  if (!props.app) return
  switching.value = true
  error.value = ''
  try {
    const data = await appsApi.switchBranch(props.app.name, branch.value)
    if (data.task_id) {
      open.value = false
      openTaskDetailPage(router, data.task_id)
    } else error.value = apiErrorMessage(data, 'Could not start the branch switch.')
  } catch (e) {
    error.value = errorMessage(e, 'Could not start the branch switch.')
  } finally {
    switching.value = false
  }
}
</script>

<template>
  <ActionDialog
    v-model:open="open"
    title="Switch branch"
    :error="error"
    confirm-label="Switch branch"
    :loading="switching"
    :disabled="!branch"
    @confirm="confirm"
  >
    <p class="text-ink-gray-6 text-p-sm">
      {{ app?.title }} is on {{ app?.branch || 'a detached commit' }}. This applies to every site.
    </p>
    <Select
        v-model="branch"
        label="New branch"
        :options="options"
        :placeholder="loadingBranches ? 'Loading branches…' : 'Choose a branch'"
        :disabled="loadingBranches"
      />
  </ActionDialog>
</template>
