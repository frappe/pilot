<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Badge, Button, Dialog, ErrorMessage, FormControl } from 'frappe-ui'
import { benchesApi } from '@/api/benches'
import { sitesApi } from '@/api/sites'
import { apiErrorMessage, hasApiError } from '@/api/client'
import { useSession } from '@/composables/auth/useSession'
import { errorMessage } from '@/utils/error'

const props = defineProps<{ kind: 'bench' | 'site'; source: string }>()
const open = defineModel<boolean>({ default: false })
const emit = defineEmits<{ started: [taskId: string] }>()
const { session } = useSession()
const name = ref('')
const targetBench = ref('')
const benches = ref<string[]>([])
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const branchMode = ref('default')
const branchName = ref('')
const appBranches = ref('')
const isSite = computed(() => props.kind === 'site')
const title = computed(() => (isSite.value ? 'Clone Site' : 'Clone Bench'))
const sameBench = computed(() => targetBench.value === session.benchName)

watch(open, async (value) => {
  if (!value) return
  name.value = ''
  branchMode.value = 'default'
  branchName.value = ''
  appBranches.value = ''
  error.value = ''
  targetBench.value = session.benchName
  benches.value = [session.benchName]
  if (!isSite.value || !session.allowBenchManagement) return
  loading.value = true
  try {
    benches.value = (await benchesApi.list()).map((bench) => bench.name)
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not load destination benches')
  } finally {
    loading.value = false
  }
})

const submit = async () => {
  if (!name.value.trim() || submitting.value) return
  error.value = ''
  submitting.value = true
  try {
    const overrides: Record<string, string> = {}
    if (!isSite.value) {
      for (const entry of appBranches.value.split(',').filter((value) => value.trim())) {
        const separator = entry.indexOf('=')
        const app = entry.slice(0, separator).trim()
        const branch = entry.slice(separator + 1).trim()
        if (separator < 1 || !app || !branch)
          throw new Error('Use app=branch overrides, such as frappe=develop')
        overrides[app] = branch
      }
      if (branchMode.value === 'named' && !branchName.value.trim())
        throw new Error('Enter a branch name')
    }
    const result = isSite.value
      ? await sitesApi.clone(props.source, name.value.trim(), targetBench.value)
      : await benchesApi.clone(
          props.source,
          name.value.trim(),
          branchMode.value === 'named' ? branchName.value.trim() : branchMode.value,
          overrides,
        )
    if (hasApiError(result)) throw new Error(apiErrorMessage(result, 'Could not start clone'))
    if (!result.task_id) throw new Error('The server did not return a clone task')
    open.value = false
    emit('started', result.task_id)
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not start clone')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Dialog v-model="open" :title="title" size="lg">
    <form class="flex flex-col gap-5" @submit.prevent="submit">
      <p class="text-p-sm text-ink-gray-6 leading-relaxed">
        {{
          isSite
            ? 'Create a fresh copy of this site for testing or UAT.'
            : 'Copy this bench’s apps and dependencies into a new development bench.'
        }}
      </p>

      <div
        class="flex items-center gap-3 rounded-6 border border-outline-gray-2 bg-surface-gray-1 p-3"
      >
        <span
          :class="isSite ? 'lucide-globe' : 'lucide-server'"
          class="size-5 text-ink-gray-5 shrink-0"
        />
        <div class="min-w-0">
          <p class="text-p-xs text-ink-gray-5">
            {{ isSite ? 'Source site' : 'Source bench' }}
          </p>
          <p class="mt-1 text-p-sm font-medium text-ink-gray-9 truncate">
            {{ source }}
          </p>
        </div>
      </div>

      <FormControl
        v-if="isSite"
        v-model="targetBench"
        type="select"
        label="Destination bench"
        :options="benches.map((value) => ({ label: value, value }))"
        :disabled="loading || submitting"
      />
      <FormControl
        v-model="name"
        :label="isSite ? 'New site name' : 'New bench name'"
        :placeholder="isSite ? 'uat.localhost' : 'uat'"
        :disabled="submitting"
        autofocus
      />
      <template v-if="!isSite">
        <FormControl
          v-model="branchMode"
          type="select"
          label="App branches"
          :disabled="submitting"
          :options="[
            { label: 'Default branch (each app’s origin)', value: 'default' },
            { label: 'Current checkout (including local changes)', value: 'current' },
            { label: 'Choose a branch', value: 'named' },
          ]"
        />
        <FormControl
          v-if="branchMode === 'named'"
          v-model="branchName"
          label="Branch name"
          placeholder="develop"
          :disabled="submitting"
        />
        <FormControl
          v-model="appBranches"
          label="Per-app overrides (optional)"
          placeholder="frappe=develop, my_app=feature/example"
          :disabled="submitting"
        />
        <p class="text-p-xs text-ink-gray-5">
          Default and named branches use clean checkouts from origin. Local changes are included
          only with Current checkout. Different branches require fresh dependencies and an asset
          build.
        </p>
      </template>

      <div class="flex flex-col gap-3 text-p-sm text-ink-gray-6">
        <template v-if="isSite">
          <p class="flex items-start gap-2">
            <span class="lucide-database size-4 mt-0.5 shrink-0" /> A separate database, credentials
            and copy of public and private files.
          </p>
          <p class="flex items-start gap-2">
            <span class="lucide-code size-4 mt-0.5 shrink-0" />
            {{
              sameBench
                ? 'App code is shared with the source. Clone the bench first to test code changes independently.'
                : 'Uses the destination bench’s app code. Its apps must support the copied database.'
            }}
          </p>
          <div class="flex flex-wrap gap-2">
            <Badge label="Scheduler paused" theme="amber" /><Badge
              label="Outgoing mail disabled"
              theme="gray"
            />
          </div>
        </template>
        <template v-else>
          <p class="flex items-start gap-2">
            <span class="lucide-git-branch size-4 mt-0.5 shrink-0" />Independent app files and Git
            repositories, using the selected branches.
          </p>
          <p class="flex items-start gap-2">
            <span class="lucide-globe size-4 mt-0.5 shrink-0" />No sites are copied. Clone a site
            into this bench when it’s ready.
          </p>
        </template>
      </div>

      <ErrorMessage v-if="error" :message="error" />
      <div class="flex justify-end gap-2">
        <Button @click="open = false" :disabled="submitting">Cancel</Button>
        <Button
          type="submit"
          variant="solid"
          :loading="submitting"
          :disabled="!name.trim() || loading"
          >{{ title }}</Button
        >
      </div>
    </form>
  </Dialog>
</template>
