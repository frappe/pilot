<script setup lang="ts">
import { useTranslation } from '../translation'
import { installApp, isMigrationConflict, uninstallApp, updateApps } from '@frappe/cloud-sdk/api'
import type { MarketplaceApp, TaskSubmission } from '@frappe/cloud-sdk'
import { Button, ErrorMessage, Select, TextInput, toast } from 'frappe-ui'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import ActionableError from '../components/ActionableError.vue'
import AppRow from '../components/AppRow.vue'
import Panel from '../components/Panel.vue'
import UninstallAppDialog from '../components/UninstallAppDialog.vue'
import UpdateAppsDialog from '../components/UpdateAppsDialog.vue'
import {
  useErrorMessage,
  getRememberedTasks,
  rememberTask,
  type Store,
  waitForTask,
} from '../store'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store
const site = store.state.context.site_name || window.location.host

const ACTION = {
  install: {
    progress: __('Installing'),
    done: __('installed'),
    verb: __('install'),
  },
  uninstall: {
    progress: __('Uninstalling'),
    done: __('uninstalled'),
    verb: __('uninstall'),
  },
  disable: {
    progress: __('Disabling'),
    done: __('disabled'),
    verb: __('disable'),
  },
  update: { progress: __('Updating'), done: __('updated'), verb: __('update') },
}

const query = ref('')
const category = ref('')
const sortBy = ref('stars')
const pending = reactive<Record<string, string>>({})
const errors = reactive<Record<string, string>>({})
const showUpdates = ref(false)
const updatingAll = ref(false)
const updateAllError = ref('')
const blocker = ref<{ message: string; actionLabel: string; actionUrl: string } | null>(null)
const uninstallTarget = ref<MarketplaceApp | null>(null)
const showUninstall = ref(false)

let gone = false

onBeforeUnmount(() => (gone = true))

watch(
  () => props.active,
  async (active) => {
    if (!active) return

    await store.loadMarketplace()

    resumeTasks()
  },
  { immediate: true },
)

const marketplace = computed(() => store.state.marketplace)

const appTitles = computed(() =>
  Object.fromEntries((marketplace.value?.apps || []).map((app) => [app.name, app.title])),
)
const error = computed(() => store.state.marketplaceError)
const loadFailed = computed(() => Boolean(error.value) && !marketplace.value)
const updateCount = computed(() => marketplace.value?.update_count || 0)
const canDisable = computed(() => Boolean(marketplace.value?.can_disable))
const appsWithUpdates = computed(() =>
  (marketplace.value?.apps || []).filter((app) => app.has_update),
)

const categoryIcons: Record<string, string> = {
  Applications: 'lucide-app-window',
  Compliance: 'lucide-shield-check',
  'Developer Tools': 'lucide-code',
  Extensions: 'lucide-puzzle',
  Integrations: 'lucide-plug',
  Utilities: 'lucide-wrench',
}

const categoryOptions = computed(() => [
  { label: __('All categories'), value: '', icon: 'lucide-layout-grid' },
  ...(marketplace.value?.categories || []).map((c) => {
    const option = typeof c === 'string' ? { label: c, value: c } : c

    return { ...option, icon: categoryIcons[option.value] || 'lucide-tag' }
  }),
])

const filteredApps = computed(() => {
  const term = query.value.trim().toLowerCase()

  return (marketplace.value?.apps || []).filter((app) => {
    if (category.value && app.category !== category.value) return false
    if (!term) return true

    return `${app.title} ${app.description}`.toLowerCase().includes(term)
  })
})

const sortOptions = computed(() => [
  { label: __('Most stars'), value: 'stars' },
  { label: __('Name'), value: 'name' },
])

const sortedApps = computed(() =>
  [...filteredApps.value].sort((a, b) =>
    sortBy.value === 'name' ? a.title.localeCompare(b.title) : (b.stars || 0) - (a.stars || 0),
  ),
)

const sections = computed(() =>
  [
    { label: __('Installed'), apps: sortedApps.value.filter((app) => app.installed) },
    { label: __('Available'), apps: sortedApps.value.filter((app) => !app.installed) },
  ].filter((section) => section.apps.length),
)

const clearFilters = () => {
  query.value = ''
  category.value = ''
}

const install = (app: MarketplaceApp) => runAction(app, 'install', () => installApp(app.name))

const askUninstall = (app: MarketplaceApp) => {
  uninstallTarget.value = app
  showUninstall.value = true
}

const uninstall = (app: MarketplaceApp, mode: 'uninstall' | 'disable') => runAction(app, mode, () => uninstallApp(app.name, mode))
const updateOne = (app: MarketplaceApp) => runAction(app, 'update', () => updateApps([app.name]))

const asBlocker = (exception: unknown) => {
  if (!isMigrationConflict(exception)) return null

  const server = store.state.context.server_url

  return {
    message: getErrorMessage(exception),
    actionLabel: server ? __('Open updates') : '',
    actionUrl: server ? `${server.replace(/\/$/, '')}/updates` : '',
  }
}

const updateAll = async ({ apps, taskId }: { apps?: string[]; taskId?: string }) => {
  let finished = false
  updatingAll.value = true
  updateAllError.value = ''
  blocker.value = null

  try {
    const { task_id } = taskId ? { task_id: taskId } : await updateApps(apps)

    rememberTask(site, '*', { taskId: task_id, verb: 'update' })

    const done = await settle(task_id, ACTION.update, __('all apps'))
    finished = done === true

    await store.loadMarketplace(true)

    if (done) toast.success(__('{0} {1}.', [__('All apps'), ACTION.update.done]))

    showUpdates.value = false
  } catch (exception) {
    finished = true
    blocker.value = asBlocker(exception)

    if (blocker.value) {
      showUpdates.value = false
    } else {
      updateAllError.value = getErrorMessage(exception)

      if (!showUpdates.value) toast.error(updateAllError.value)
    }
  } finally {
    if (finished) rememberTask(site, '*')
    updatingAll.value = false
  }
}

const runAction = async (app: Pick<MarketplaceApp, 'name' | 'title'>, verb: keyof typeof ACTION, action: () => TaskSubmission | Promise<TaskSubmission>) => {
  let finished = false
  errors[app.name] = ''
  blocker.value = null
  pending[app.name] = verb

  try {
    const { task_id } = await action()

    rememberTask(site, app.name, { taskId: task_id, verb })

    const done = await settle(task_id, ACTION[verb], app.title)
    finished = done === true

    delete errors[app.name]
    await store.loadMarketplace(true)

    if (!done) return

    const current = marketplace.value?.apps?.find((row) => row.name === app.name)

    if (verb === 'uninstall' && current?.installed) {
      throw new Error(
        __("Couldn't uninstall {0}. Another installed app may depend on it.", [app.title]),
      )
    }

    toast.success(__('{0} {1}.', [app.title, ACTION[verb].done]))
  } catch (exception) {
    finished = true
    blocker.value = asBlocker(exception)

    if (!blocker.value) {
      errors[app.name] = getErrorMessage(exception)

      toast.error(errors[app.name])
    }
  } finally {
    if (finished) rememberTask(site, app.name)
    delete pending[app.name]
  }
}

const resumeTasks = () => {
  const tasks = getRememberedTasks(site)

  if (tasks['*'] && !updatingAll.value) updateAll({ taskId: tasks['*'].taskId })

  for (const [name, task] of Object.entries(tasks)) {
    if (name === '*' || pending[name]) continue

    const app = marketplace.value?.apps?.find((row) => row.name === name) || { name, title: name }

    if (task.verb === 'install' || task.verb === 'uninstall' || task.verb === 'disable' || task.verb === 'update') {
      runAction(app, task.verb, () => ({ task_id: task.taskId }))
    }
  }
}

const settle = async (taskId: string, action: { verb: string; progress: string }, label: string) => {
  if (!taskId) return true

  const outcome = await waitForTask(taskId, () => gone)

  if (outcome === 'success') return true
  if (outcome === 'failed' || outcome === 'error') {
    throw new Error(__("Couldn't {0} {1}.", [action.verb, label]))
  }

  if (outcome !== 'cancelled') {
    toast.warning(
      __(
        '{0} {1} is taking longer than expected. It will keep running in the background. Reopen to check.',
        [action.progress, label],
      ),
    )
  }
}

</script>

<template>
  <Panel
    :title="__('Marketplace')"
    :description="__('Install apps and keep them up to date.')"
    :loading="!marketplace && !error"
    :error="loadFailed ? error : ''"
    :error-title="__(`Couldn't load the marketplace`)"
    @retry="store.loadMarketplace(true)"
  >
    <template #actions>
      <Button
        v-if="updateCount"
        class="col-start-2 row-span-2 row-start-1"
        variant="solid"
        :loading="updatingAll"
        :label="updatingAll ? __('Updating') : __('Update all ({0})', [updateCount])"
        @click="((updateAllError = ''), (showUpdates = true))"
      />
    </template>

    <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
      <TextInput v-model="query" class="flex-1" :aria-label="__('Search apps')" :placeholder="__('Search apps')">
        <template #prefix><span class="lucide-search size-4 text-ink-gray-5" /></template>
      </TextInput>

      <Select v-model="category" :aria-label="__('Category')" class="sm:w-44" :options="categoryOptions" side="bottom" />

      <Select v-model="sortBy" :aria-label="__('Sort by')" class="sm:w-36" :options="sortOptions" side="bottom">
        <template #prefix><span class="lucide-arrow-down-up size-4 text-ink-gray-5" /></template>
      </Select>
    </div>

    <ErrorMessage :message="loadFailed ? '' : error" class="mt-2" />

    <ActionableError
      v-if="blocker"
      class="mt-4"
      :message="blocker.message"
      :action-label="blocker.actionLabel"
      :action-url="blocker.actionUrl"
    />

    <p v-if="!filteredApps.length" class="py-12 text-center text-p-sm text-ink-gray-5">
      {{ __("No apps match your search.") }}

      <Button class="mt-3 block" :label="__('Clear filters')" @click="clearFilters" />
    </p>

    <template v-for="(section, index) in sections" :key="section.label">
      <h3 class="mb-3 text-base-semibold text-ink-gray-8" :class="index ? 'mt-8' : 'mt-6'">
        {{ section.label }}
      </h3>

      <div class="grid gap-1 sm:grid-cols-2 -mx-3">
        <AppRow
          v-for="app in section.apps"
          :key="app.name"
          :app="app"
          :app-titles="appTitles"
          :pending="pending[app.name] || ''"
          :error="errors[app.name] || ''"
          @install="install"
          @uninstall="askUninstall"
          @update="updateOne"
        />
      </div>
    </template>
  </Panel>

  <UpdateAppsDialog
    v-model="showUpdates"
    :apps="appsWithUpdates"
    :updating="updatingAll"
    :error="updateAllError"
    @submit="updateAll"
  />

  <UninstallAppDialog
    v-model="showUninstall"
    :app="uninstallTarget"
    :can-disable="canDisable"
    @confirm="uninstall"
  />
</template>
