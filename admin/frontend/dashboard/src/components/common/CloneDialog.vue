<script setup lang="ts">
import { Badge, Combobox, FormControl, Skeleton } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { benchesApi } from '@/api/benches'
import { apiErrorMessage, hasApiError } from '@/api/client'
import { sitesApi } from '@/api/sites'
import AppIcon from '@/components/apps/AppIcon.vue'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useSession } from '@/composables/auth/useSession'
import type { CloneApp } from '@/types/benches'
import { errorMessage } from '@/utils/error'
import { toSentenceCase } from '@/utils/format'

const props = defineProps<{ kind: 'bench' | 'site'; source: string }>()
const open = defineModel<boolean>({ default: false })
const emit = defineEmits<{ started: [taskId: string] }>()
const { session } = useSession()
const { registry, titleMap, load: loadAppRegistry } = useAppRegistry()
const name = ref('')
const targetBench = ref('')
const benches = ref<string[]>([])
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const apps = ref<CloneApp[]>([])
const appsLoaded = ref(false)
const appBranches = ref<Record<string, string>>({})
const branchOptions = ref<Record<string, string[]>>({})
const branchErrors = ref<Record<string, string>>({})
const branchLoading = ref<Record<string, boolean>>({})
const fetchingBranches = computed(() =>
  apps.value.some((app) => !appBranches.value[app.name] && !branchErrors.value[app.name]),
)
const appTitle = (app: CloneApp) => titleMap.value[app.name] || toSentenceCase(app.title)
const marketplaceBranch = (app: string) =>
  registry.value.find((entry) => entry.name === app)?.branch || ''
const isSite = computed(() => props.kind === 'site')
const title = computed(() => (isSite.value ? 'Clone site' : 'Clone bench'))
const sameBench = computed(() => targetBench.value === session.benchName)
const canSubmit = computed(
  () =>
    name.value.trim() &&
    !loading.value &&
    (isSite.value || (appsLoaded.value && apps.value.every((app) => appBranches.value[app.name]))),
)

const fetchingItem = {
  label: 'Fetching branches…',
  value: '',
  icon: 'lucide-loader-circle animate-spin',
  disabled: true,
}

const branchItems = (app: string) => {
  const branches = (branchOptions.value[app] || [appBranches.value[app]]).map((branch) => ({
    label: branch === 'current' ? 'Local copy' : branch,
    value: branch,
  }))

  return branchLoading.value[app] ? [fetchingItem, ...branches] : branches
}

const loadBranches = async (app: string) => {
  if (branchOptions.value[app] || branchLoading.value[app]) return

  branchLoading.value[app] = true
  delete branchErrors.value[app]

  try {
    const result = await benchesApi.cloneAppBranches(props.source, app)

    if (!open.value) return
    if (hasApiError(result)) throw new Error(apiErrorMessage(result, 'Could not load branches'))

    appBranches.value[app] ||= result.default_branch
    branchOptions.value[app] = result.branches
  } catch (caught) {
    if (open.value) branchErrors.value[app] = errorMessage(caught, 'Could not load branches')
  } finally {
    branchLoading.value[app] = false
  }
}

watch(
  open,
  async (value, _previous, onCleanup) => {
    let stale = false
    onCleanup(() => {
      stale = true
    })
    if (!value) return
    name.value = ''
    apps.value = []
    appsLoaded.value = false
    appBranches.value = {}
    branchOptions.value = {}
    branchErrors.value = {}
    branchLoading.value = {}
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
        const [result] = await Promise.all([benchesApi.cloneApps(props.source), loadAppRegistry()])

        if (stale) return
        if (hasApiError(result)) throw new Error(apiErrorMessage(result, 'Could not load apps'))

        apps.value = result.apps
        appBranches.value = Object.fromEntries(
          result.apps.map((app) => [app.name, marketplaceBranch(app.name)]),
        )
        appsLoaded.value = true

        for (const app of result.apps) if (!appBranches.value[app.name]) loadBranches(app.name)
      }
    } catch (caught) {
      if (!stale)
        error.value = errorMessage(
          caught,
          isSite.value ? 'Could not load destination benches' : 'Could not load apps',
        )
    } finally {
      if (!stale) loading.value = false
    }
  },
  { immediate: true },
)

const submit = async () => {
  if (!canSubmit.value || submitting.value) return
  error.value = ''
  submitting.value = true
  try {
    const result = isSite.value
      ? await sitesApi.clone(props.source, name.value.trim(), targetBench.value)
      : await benchesApi.clone(props.source, name.value.trim(), 'default', appBranches.value)
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
  <ActionDialog
    v-model:open="open"
    :title="title"
    :error="error"
    confirm-label="Clone"
    :loading="submitting"
    :disabled="!canSubmit"
    @confirm="submit"
  >
    <p class="text-ink-gray-6 text-p-sm">
      {{ isSite
          ? 'Create a fresh copy of this site for testing or UAT.'
          : 'Copy this bench’s apps and dependencies into a new bench.' }}
    </p>

    <FormControl
      v-if="isSite"
      v-model="targetBench"
      type="select"
      label="Destination bench"
      :options="benches.map((value) => ({ label: value, value }))"
      :disabled="loading || submitting"
    />

    <div class="flex items-center gap-3">
      <div
        :class="isSite ? 'before:lucide-globe' : 'before:lucide-layers'"
        class="flex flex-1 items-center gap-2 before:size-4 before:text-ink-gray-5 before:shrink-0 border border-dashed border-outline-gray-3 px-2 rounded-4 min-w-0 h-7 text-ink-gray-8 text-base"
      >
        <span class="truncate">{{ source }}</span>
      </div>

      <span class="size-4 text-ink-gray-5 shrink-0 lucide-arrow-right" />

      <FormControl
        v-model="name"
        class="flex-1 min-w-0"
        :placeholder="isSite ? 'New site name' : 'New bench name'"
        :aria-label="isSite ? 'New site name' : 'New bench name'"
        :disabled="submitting"
        autofocus
        @keydown.enter="submit"
      />
    </div>

    <div v-if="!isSite">
      <div class="flex items-center gap-3 mb-2 text-ink-gray-6 text-sm">
        <p class="flex-1">
          Apps
          <span v-if="appsLoaded" class="text-ink-gray-5">· {{ apps.length }}</span>
        </p>

        <p
          :class="{ 'gap-1.5 before:size-3.5 before:animate-spin before:lucide-loader-circle': fetchingBranches }"
          class="flex items-center w-40 shrink-0"
        >
          {{ fetchingBranches ? 'Fetching branches…' : 'Branch' }}
        </p>
      </div>

      <ul v-if="loading" class="divide-y divide-outline-gray-1">
        <li v-for="row in 4" :key="row" class="flex items-center gap-3 py-1.5">
          <Skeleton class="rounded-4 size-5 shrink-0" />
          <Skeleton class="rounded-4 w-28 h-3.5" />
          <Skeleton class="ml-auto rounded-4 w-40 h-7 shrink-0" />
        </li>
      </ul>

      <p v-else-if="appsLoaded && !apps.length" class="text-ink-gray-5 text-p-sm">
        No apps installed.
      </p>

      <ul
        v-else-if="apps.length"
        class="-mx-1 px-1 divide-y divide-outline-gray-1 max-h-64 overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        <li v-for="app in apps" :key="app.name" class="flex items-center gap-3 py-1.5">
          <AppIcon :name="app.name" :label="appTitle(app)" size="sm" />
          <span class="flex-1 text-ink-gray-8 text-base truncate">{{ appTitle(app) }}</span>

          <Combobox
            v-if="appBranches[app.name]"
            v-model="appBranches[app.name]"
            class="w-40 shrink-0"
            placeholder="Search branches…"
            :empty-text="branchErrors[app.name] || 'No matching branch.'"
            :disabled="submitting"
            :options="branchItems(app.name)"
            @update:open="(isOpen: boolean) => isOpen && loadBranches(app.name)"
          />

          <p
            v-else-if="branchErrors[app.name]"
            class="w-40 text-ink-red-5 text-xs truncate shrink-0"
            :title="branchErrors[app.name]"
          >
            {{ branchErrors[app.name] }}
          </p>

          <Skeleton v-else class="rounded-4 w-40 h-7 shrink-0" />
        </li>
      </ul>
    </div>

    <div v-else class="space-y-3 text-ink-gray-6 text-p-sm">
      <p
        class="flex items-start gap-2 before:mt-0.5 before:size-4 before:shrink-0 before:lucide-database"
      >
        A separate database, credentials and copy of public and private files.
      </p>

      <p
        class="flex items-start gap-2 before:mt-0.5 before:size-4 before:shrink-0 before:lucide-code"
      >
        {{ sameBench
            ? 'App code is shared with the source. Clone the bench first to test code changes independently.'
            : 'Uses the destination bench’s app code. Its apps must support the copied database.' }}
      </p>

      <div class="flex flex-wrap gap-2">
        <Badge label="Scheduler paused" theme="amber" />
        <Badge label="Outgoing mail disabled" theme="gray" />
      </div>
    </div>
  </ActionDialog>
</template>
