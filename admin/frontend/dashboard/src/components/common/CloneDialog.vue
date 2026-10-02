<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Badge, Button, Dialog, ErrorMessage, FormControl } from 'frappe-ui'
import { benchesApi } from '@/api/benches'
import { sitesApi } from '@/api/sites'
import { apiErrorMessage, hasApiError } from '@/api/client'
import { useSession } from '@/composables/auth/useSession'
import { errorMessage } from '@/utils/error'
import type { CloneAppBranches } from '@/types/benches'

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
const apps = ref<CloneAppBranches[]>([])
const appBranches = ref<Record<string, string>>({})
const branchesLoaded = ref(false)
const isSite = computed(() => props.kind === 'site')
const title = computed(() => (isSite.value ? 'Clone Site' : 'Clone Bench'))
const sameBench = computed(() => targetBench.value === session.benchName)
const canSubmit = computed(
  () => name.value.trim() && !loading.value && (isSite.value || branchesLoaded.value),
)

watch(open, async (value, _previous, onCleanup) => {
  let stale = false
  onCleanup(() => {
    stale = true
  })
  if (!value) return
  name.value = ''
  apps.value = []
  appBranches.value = {}
  branchesLoaded.value = false
  loading.value = false
  error.value = ''
  targetBench.value = session.benchName
  benches.value = [session.benchName]
  if (isSite.value && !session.allowBenchManagement) return
  loading.value = true
  try {
    if (isSite.value) {
      const result = await benchesApi.list()
      if (!stale) benches.value = result.map((bench) => bench.name)
    } else {
      const result = await benchesApi.cloneBranchOptions(props.source)
      if (stale) return
      if (hasApiError(result))
        throw new Error(apiErrorMessage(result, 'Could not load app branches'))
      apps.value = result.apps
      appBranches.value = Object.fromEntries(
        result.apps.map((app) => [app.name, app.default_branch]),
      )
      branchesLoaded.value = true
    }
  } catch (caught) {
    if (!stale)
      error.value = errorMessage(
        caught,
        isSite.value ? 'Could not load destination benches' : 'Could not load app branches',
      )
  } finally {
    if (!stale) loading.value = false
  }
})

const submit = async () => {
  if (!canSubmit.value || submitting.value) return
  error.value = ''
  submitting.value = true
  try {
    const overrides = Object.fromEntries(
      apps.value
        .filter((app) => appBranches.value[app.name] !== app.default_branch)
        .map((app) => [app.name, appBranches.value[app.name]]),
    )
    const result = isSite.value
      ? await sitesApi.clone(props.source, name.value.trim(), targetBench.value)
      : await benchesApi.clone(props.source, name.value.trim(), 'default', overrides)
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
      <details v-if="!isSite" class="group/branches rounded-6 border border-outline-gray-2">
        <summary
          class="flex cursor-pointer items-center justify-between rounded-6 px-3 py-3 text-p-sm font-medium text-ink-gray-8 hover:bg-surface-gray-1"
        >
          <span class="flex items-center gap-2"
            ><span class="lucide-git-branch size-4 text-ink-gray-5" />App branches</span
          >
          <span
            class="lucide-chevron-down size-4 text-ink-gray-5 transition-transform group-open/branches:rotate-180"
          />
        </summary>
        <div class="flex flex-col gap-4 px-3 pb-3">
          <p v-if="loading" class="text-p-sm text-ink-gray-5">Loading branches…</p>
          <FormControl
            v-for="app in apps"
            :key="app.name"
            v-model="appBranches[app.name]"
            type="select"
            :label="app.name"
            :disabled="submitting"
            :options="app.branches.map((branch) => ({ label: branch, value: branch }))"
          />
          <p v-if="branchesLoaded && !apps.length" class="text-p-sm text-ink-gray-5">
            No apps installed.
          </p>
        </div>
      </details>

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
        <Button type="submit" variant="solid" :loading="submitting" :disabled="!canSubmit">{{
          title
        }}</Button>
      </div>
    </form>
  </Dialog>
</template>
