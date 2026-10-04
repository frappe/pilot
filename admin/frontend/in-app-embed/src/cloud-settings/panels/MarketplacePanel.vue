<script setup lang="ts">
import { Button, ErrorMessage, Select, TextInput } from 'frappe-ui'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import ActionableError from '../components/ActionableError.vue'
import AppRow from '../components/AppRow.vue'
import Panel from '../components/Panel.vue'
import UninstallAppDialog from '../components/UninstallAppDialog.vue'
import UpdateAppsDialog from '../components/UpdateAppsDialog.vue'
import { type Store, getRememberedTasks, rememberTask, waitForTask } from '../store'

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
const pending = reactive({})
const errors = reactive({})
const showUpdates = ref(false)
const updatingAll = ref(false)
const updateAllError = ref('')
const blocker = ref(null)
const uninstallTarget = ref(null)
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
const error = computed(() => store.state.marketplaceError)
const loadFailed = computed(() => Boolean(error.value) && !marketplace.value)
const updateCount = computed(() => marketplace.value?.update_count || 0)
const canDisable = computed(() => Boolean(marketplace.value?.can_disable))
const appsWithUpdates = computed(() =>
  (marketplace.value?.apps || []).filter((app) => app.has_update),
)

const categoryOptions = computed(() => [
  { label: __('All categories'), value: '' },
  ...(marketplace.value?.categories || []).map((c) =>
    typeof c === 'string' ? { label: c, value: c } : c,
  ),
])

const filteredApps = computed(() => {
  const term = query.value.trim().toLowerCase()

  return (marketplace.value?.apps || []).filter((app) => {
    if (category.value && app.category !== category.value) return false
    if (!term) return true

    return `${app.title} ${app.description}`.toLowerCase().includes(term)
  })
})

const sections = computed(() =>
  [
    { label: __('Installed'), apps: filteredApps.value.filter((app) => app.installed) },
    { label: __('Available'), apps: filteredApps.value.filter((app) => !app.installed) },
  ].filter((section) => section.apps.length),
)

const clearFilters = () => {
  query.value = ''
  category.value = ''
}

const install = (app) => runAction(app, 'install', () => store.api.installApp(app.name))

const askUninstall = (app) => {
  uninstallTarget.value = app
  showUninstall.value = true
}

const uninstall = (app, mode) => runAction(app, mode, () => store.api.uninstallApp(app.name, mode))
const updateOne = (app) => runAction(app, 'update', () => store.api.updateApps([app.name]))

const asBlocker = (exception) => {
  if (!store.api.isMigrationConflict(exception)) return null

  const server = store.state.context.server_url

  return {
    message: store.api.getErrorMessage(exception),
    actionLabel: server ? __('Open updates') : '',
    actionUrl: server ? `${server.replace(/\/$/, '')}/updates` : '',
  }
}

const updateAll = async ({ apps, taskId }) => {
  updatingAll.value = true
  updateAllError.value = ''
  blocker.value = null

  try {
    const { task_id } = taskId ? { task_id: taskId } : await store.api.updateApps(apps)

    rememberTask(site, '*', { taskId: task_id, verb: 'update' })

    const done = await settle(task_id, ACTION.update, __('all apps'))

    await store.loadMarketplace(true)

    if (done) notify(__('{0} {1}.', [__('All apps'), ACTION.update.done]), 'green')

    showUpdates.value = false
  } catch (exception) {
    blocker.value = asBlocker(exception)

    if (blocker.value) {
      showUpdates.value = false
    } else {
      updateAllError.value = store.api.getErrorMessage(exception)

      if (!showUpdates.value) notify(updateAllError.value, 'red')
    }
  } finally {
    rememberTask(site, '*')
    updatingAll.value = false
  }
}

const runAction = async (app, verb, action) => {
  errors[app.name] = ''
  blocker.value = null
  pending[app.name] = verb

  try {
    const { task_id } = await action()

    rememberTask(site, app.name, { taskId: task_id, verb })

    const done = await settle(task_id, ACTION[verb], app.title)

    delete errors[app.name]
    await store.loadMarketplace(true)

    if (!done) return

    const current = marketplace.value?.apps?.find((row) => row.name === app.name)

    if (verb === 'uninstall' && current?.installed) {
      throw new Error(
        __("Couldn't uninstall {0}. Another installed app may depend on it.", [app.title]),
      )
    }

    notify(__('{0} {1}.', [app.title, ACTION[verb].done]), 'green')
  } catch (exception) {
    blocker.value = asBlocker(exception)

    if (!blocker.value) {
      errors[app.name] = store.api.getErrorMessage(exception)

      notify(errors[app.name], 'red')
    }
  } finally {
    rememberTask(site, app.name)
    delete pending[app.name]
  }
}

const resumeTasks = () => {
  const tasks = getRememberedTasks(site)

  if (tasks['*'] && !updatingAll.value) updateAll({ taskId: tasks['*'].taskId })

  for (const [name, task] of Object.entries(tasks)) {
    if (name === '*' || pending[name]) continue

    const app = marketplace.value?.apps?.find((row) => row.name === name) || { name, title: name }

    runAction(app, task.verb, () => ({ task_id: task.taskId }))
  }
}

const settle = async (taskId, action, label) => {
  if (!taskId) return true

  const outcome = await waitForTask(taskId, () => gone)

  if (outcome === 'success') return true
  if (outcome === 'failed' || outcome === 'error') {
    throw new Error(__("Couldn't {0} {1}.", [action.verb, label]))
  }

  if (outcome !== 'cancelled') {
    notify(
      __(
        '{0} {1} is taking longer than expected. It will keep running in the background — reopen to check.',
        [action.progress, label],
      ),
      'orange',
    )
  }
}

const notify = (message, indicator = 'green') => {
  frappe.show_alert({ message, indicator })
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
      <TextInput v-model="query" class="flex-1" :placeholder="__('Search apps')" />

      <Select v-model="category" class="sm:w-44" :options="categoryOptions" />
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

      <div class="grid gap-x-6 gap-y-4 sm:grid-cols-2">
        <AppRow
          v-for="app in section.apps"
          :key="app.name"
          :app="app"
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
