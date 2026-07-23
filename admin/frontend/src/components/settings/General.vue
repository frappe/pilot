<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Switch label="Developer mode"
      description="Lets developers customise doctypes on every site in this bench."
      :model-value="developerMode" :disabled="savingDeveloperMode"
      @update:model-value="toggleDeveloperMode" />

    <div class="flex sm:flex-row sm:justify-between sm:items-center flex-col gap-3">
      <div>
        <p class="flex items-center gap-2 font-medium text-ink-gray-8 text-sm">
          {{ status.current_version || 'Unknown version' }}
          <span v-if="status.is_dev"
            class="px-1.5 py-0.5 rounded bg-surface-gray-3 font-normal text-ink-gray-6 text-xs">dev</span>
        </p>
        <p class="text-ink-gray-5 text-p-sm">{{ subtitle }}</p>
      </div>
      <div v-if="!status.is_dev" class="flex items-center gap-2">
        <Button class="flex-1 sm:flex-none" variant="subtle" :loading="checking"
          icon-left="lucide-refresh-cw" @click="check">Check for updates</Button>
        <Button v-if="updateAvailable" class="flex-1 sm:flex-none" variant="solid" :loading="updating"
          icon-left="lucide-download" @click="update">Update to {{ latestVersion }}</Button>
      </div>
    </div>

    <p v-if="status.is_dev" class="text-ink-gray-5 text-p-sm">
      Development install — update with <code class="text-xs">git pull</code> or
      <code class="text-xs">bench admin upgrade</code>.
    </p>

    <pre v-if="log"
      class="p-3 bg-surface-gray-2 rounded max-h-48 overflow-auto text-ink-gray-7 text-xs whitespace-pre-wrap">{{ log }}</pre>
    <ErrorMessage v-if="error" :message="error" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Button, ErrorMessage, Switch, toast } from 'frappe-ui'
import { cliUpdatesApi, settingsApi } from '@/api/settings'
import { tasksApi } from '@/api/tasks'
import { isTaskActive } from '@/utils/taskFormat'

const POLL_INTERVAL_MS = 1500

const loading = ref(true)
const checking = ref(false)
const checked = ref(false)
const updating = ref(false)
const status = ref({ current_version: '', is_dev: true })
const latestVersion = ref(null)
const log = ref('')
const error = ref(null)
const developerMode = ref(false)
const savingDeveloperMode = ref(false)

const updateAvailable = computed(() => Boolean(latestVersion.value) && latestVersion.value !== status.value.current_version)

const subtitle = computed(() => {
  if (status.value.is_dev) return 'Development build'
  if (updating.value) return 'Updating…'
  if (updateAvailable.value) return `Update available: ${latestVersion.value}`
  if (checked.value) return 'You are on the latest version'
  return 'Released build'
})

onMounted(async () => {
  const [versionResult, settingsResult] = await Promise.allSettled([
    cliUpdatesApi.status(),
    settingsApi.get(),
  ])
  if (versionResult.status === 'fulfilled') status.value = versionResult.value
  else error.value = 'Could not load version information.'
  if (settingsResult.status === 'fulfilled') {
    developerMode.value = Boolean(settingsResult.value?.bench?.developer_mode)
  }
  loading.value = false
})

async function toggleDeveloperMode(value) {
  savingDeveloperMode.value = true
  error.value = null
  try {
    await settingsApi.update({ bench: { developer_mode: value } })
    developerMode.value = value
    toast.success(`Developer mode ${value ? 'enabled' : 'disabled'}`)
  } catch (e) {
    error.value = e.message || 'Could not update developer mode.'
  } finally {
    savingDeveloperMode.value = false
  }
}

async function check() {
  if (checking.value) return
  checking.value = true
  error.value = null
  try {
    const result = await cliUpdatesApi.check()
    status.value = { ...status.value, ...result }
    latestVersion.value = result.latest_version
    checked.value = true
  } catch {
    error.value = 'Could not check for updates.'
  } finally {
    checking.value = false
  }
}

async function update() {
  if (updating.value) return
  updating.value = true
  error.value = null
  log.value = 'Starting update...'
  try {
    const { task_id } = await tasksApi.run('update-cli')
    await pollTask(task_id)
  } catch {
    error.value = 'Update failed. Check the Tasks view for details.'
  } finally {
    updating.value = false
  }
}

async function pollTask(taskId) {
  // The admin service restarts mid-update, so detail requests fail transiently.
  // Give it a bounded window to come back before declaring the update lost.
  const MAX_CONSECUTIVE_FAILURES = 40 // ~60s at POLL_INTERVAL_MS
  let failures = 0
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    let task
    try {
      task = await tasksApi.detail(taskId)
      failures = 0
    } catch {
      failures += 1
      if (failures >= MAX_CONSECUTIVE_FAILURES) {
        error.value = 'Lost contact with the admin service after the update. Check the Tasks view.'
        return
      }
      continue
    }
    log.value = (await tasksApi.output(taskId)) || log.value
    if (isTaskActive(task)) continue
    if (task.status !== 'success') error.value = 'Update did not complete successfully.'
    else status.value = await cliUpdatesApi.status().catch(() => status.value)
    return
  }
}
</script>
