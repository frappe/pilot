<script setup lang="ts">
import { useTranslation } from '../translation'
import { installApp, isMigrationConflict, uninstallApp, updateApps } from '@frappe/cloud-sdk/api'
import type { MarketplaceApp, TaskSubmission } from '@frappe/cloud-sdk'
import { Button, ErrorMessage, Select, TextInput, toast } from 'frappe-ui'
import { computed, inject, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import ActionableError from '../components/ActionableError.vue'
import AppRow from '../components/AppRow.vue'
import AppTaskList from '../components/AppTaskList.vue'
import Panel from '../components/Panel.vue'
import UninstallAppDialog from '../components/UninstallAppDialog.vue'
import UpdateAppsDialog from '../components/UpdateAppsDialog.vue'
import {
  appTasks,
  useErrorMessage,
  getRememberedTasks,
  rememberTask,
  type Store,
  type TaskOutcome,
  watchAppTask,
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
const hostApp = inject('hostApp', '')

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
const appLogos = computed(() =>
  Object.fromEntries((marketplace.value?.apps || []).map((app) => [app.name, app.logo_url])),
)

const listFormat = new Intl.ListFormat(undefined, { type: 'conjunction' })

// The site exists to try its trial app, so that app stays until the trial converts, and so do the apps it needs.
const getUninstallBlockedReason = (app: MarketplaceApp) => {
  if (app.name === store.state.billing?.trial?.app) return __('Your trial is for {0}. Upgrade to remove it.', [app.title])

  const dependents = (app.required_by || []).map((name) => appTitles.value[name] || name)
  return dependents.length ? __('Needed by {0}.', [listFormat.format(dependents)]) : ''
}

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

// An app with a progress card shows its state there only, not in its row as well.
const sections = computed(() => {
  const listed = sortedApps.value.filter((app) => !cardTasks.value.some((item) => item.name === app.name))

  return [
    { label: __('Installed'), apps: listed.filter((app) => app.installed) },
    { label: __('Available'), apps: listed.filter((app) => !app.installed) },
  ].filter((section) => section.apps.length)
})

// Install and uninstall run as tasks that take minutes, so they get a progress card.
const isCardVerb = (verb: string) => verb === 'install' || verb === 'uninstall'

const cardTasks = computed(() =>
  Object.entries(appTasks)
    .filter(([name, entry]) => name !== '*' && isCardVerb(entry.verb))
    .map(([name, entry]) => ({
      ...entry,
      verb: entry.verb as 'install' | 'uninstall',
      name,
      title: appTitles.value[name] || name,
      logoUrl: appLogos.value[name] || '',
    })),
)

const content = ref<HTMLElement>()
const scrolledAt = ref(0)
provide('marketplaceScrolledAt', scrolledAt)

const onScroll = () => (scrolledAt.value = performance.now())
// The panel scrolls inside a shadow root, which a scroll event does not leave.
const getScrollRoot = () => content.value?.getRootNode() as Document | ShadowRoot | undefined

onMounted(() => getScrollRoot()?.addEventListener('scroll', onScroll, { capture: true, passive: true }))
onBeforeUnmount(() => getScrollRoot()?.removeEventListener('scroll', onScroll, { capture: true }))

const retryTask = (item: { name: string; title: string; verb: string }) =>
  item.verb === 'uninstall' ? uninstall(item, 'uninstall') : install(item)

// A trial can't open its server, so its failed card offers a copy for support instead.
const getTaskUrl = (taskId: string) => {
  const server = store.state.context.server_url
  return server && !store.isOnTrial() ? `${server.replace(/\/$/, '')}/insights/tasks/${taskId}` : ''
}

const dismissTask = (name: string) => {
  delete appTasks[name]
  delete errors[name]
}

const clearFilters = () => {
  query.value = ''
  category.value = ''
}

const install = (app: Pick<MarketplaceApp, 'name' | 'title'>) => runAction(app, 'install', () => installApp(app.name))

const askUninstall = (app: MarketplaceApp) => {
  uninstallTarget.value = app
  showUninstall.value = true
}

const uninstall = (app: Pick<MarketplaceApp, 'name' | 'title'>, mode: 'uninstall' | 'disable') => runAction(app, mode, () => uninstallApp(app.name, mode))
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

// The page loaded before the change, so a reload shows the app's pages.
const toastDone = (label: string, verb: keyof typeof ACTION) =>
  toast.success(__('{0} {1}.', [label, ACTION[verb].done]), {
    action: { label: __('Reload'), onClick: () => window.location.reload() },
    duration: 10000,
  })

// A watch outlives the panel. A reopened panel joins it and leaves the toasts to the first watcher.
const watchTask = (name: string, taskId: string, verb: keyof typeof ACTION) => {
  if (!taskId) return undefined

  rememberTask(site, name, { taskId, verb })
  return watchAppTask(name, taskId, verb)
}

const updateAll = async ({ apps, taskId }: { apps?: string[]; taskId?: string }) => {
  let finished = false
  let isJoined = false
  updatingAll.value = true
  updateAllError.value = ''
  blocker.value = null

  try {
    const { task_id } = taskId ? { task_id: taskId } : await updateApps(apps)
    const watch = watchTask('*', task_id, 'update')
    isJoined = Boolean(watch?.isJoined)

    const done = await settle(watch?.outcome, ACTION.update, __('all apps'))
    finished = done === true

    await store.loadMarketplace(true)

    if (done && !isJoined) toastDone(__('All apps'), 'update')

    showUpdates.value = false
  } catch (exception) {
    finished = true
    blocker.value = asBlocker(exception)

    if (blocker.value) {
      showUpdates.value = false
    } else {
      updateAllError.value = getErrorMessage(exception)

      if (!showUpdates.value && !isJoined) toast.error(updateAllError.value)
    }
  } finally {
    if (finished) {
      rememberTask(site, '*')
      delete appTasks['*']
    }
    updatingAll.value = false
  }
}

const runAction = async (app: Pick<MarketplaceApp, 'name' | 'title'>, verb: keyof typeof ACTION, action: () => TaskSubmission | Promise<TaskSubmission>) => {
  let finished = false
  let isJoined = false
  errors[app.name] = ''
  blocker.value = null
  pending[app.name] = verb

  try {
    const { task_id } = await action()
    const watch = watchTask(app.name, task_id, verb)
    isJoined = Boolean(watch?.isJoined)

    const done = await settle(watch?.outcome, ACTION[verb], app.title)
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

    // This page belongs to the app that was just removed, so it no longer exists.
    if ((verb === 'uninstall' || verb === 'disable') && app.name === hostApp) {
      window.location.assign('/desk')
      return
    }

    if (!isJoined) toastDone(app.title, verb)
  } catch (exception) {
    finished = true
    blocker.value = asBlocker(exception)

    if (!blocker.value) {
      errors[app.name] = getErrorMessage(exception)

      if (!isJoined) toast.error(errors[app.name], { description: appTasks[app.name]?.task?.error || undefined })
    }
  } finally {
    if (finished) rememberTask(site, app.name)
    // A failed card stays until the user dismisses it or tries again.
    if (finished && !(isCardVerb(verb) && errors[app.name])) delete appTasks[app.name]
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

const settle = async (wait: Promise<TaskOutcome> | undefined, action: { verb: string; progress: string }, label: string) => {
  if (!wait) return true

  const outcome = await wait

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
    skeleton="apps"
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

    <div ref="content" class="flex flex-col gap-2 sm:flex-row sm:items-center">
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

    <AppTaskList :items="cardTasks" :get-task-url="getTaskUrl" @retry="retryTask" @dismiss="dismissTask" />

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
          :uninstall-blocked-reason="getUninstallBlockedReason(app)"
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
    :is-trial="store.isOnTrial()"
    @confirm="uninstall"
  />
</template>
