<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Switch label="Allow developer mode"
      description="Lets developer mode be turned on per site from each site's settings."
      :model-value="allowDeveloperMode" :disabled="savingDeveloperMode"
      @update:model-value="toggleAllowDeveloperMode" />

    <div>
      <p class="mb-2 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">Version</p>
      <div class="flex justify-between items-center gap-3">
        <p class="font-medium text-ink-gray-8 text-sm">{{ versionLabel }}</p>
        <Button variant="subtle" :loading="checking" @click="check">Check updates</Button>
      </div>
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <Dialog v-model="dialogOpen" :options="{ title: 'Update', size: 'md' }">
      <template #body-content>
        <div v-if="isDev" class="flex flex-col gap-3">
          <p class="text-ink-gray-7 text-sm">This is a development install. Update it from a terminal:</p>
          <pre
            class="p-3 bg-surface-gray-2 rounded overflow-x-auto text-ink-gray-8 text-xs">git pull
bench admin build
bench admin upgrade</pre>
          <p class="text-ink-gray-5 text-p-sm">The last step restarts the admin service.</p>
        </div>

        <div v-else-if="updating" class="flex flex-col gap-3">
          <p class="text-ink-gray-7 text-sm">Updating to {{ latestVersion }}…</p>
          <pre v-if="log"
            class="p-3 bg-surface-gray-2 rounded max-h-64 overflow-auto text-ink-gray-7 text-xs whitespace-pre-wrap">{{ log }}</pre>
        </div>

        <div v-else-if="updateAvailable" class="flex flex-col gap-3">
          <p class="text-ink-gray-7 text-sm">
            Version <strong>{{ latestVersion }}</strong> is available. You are on
            {{ status.current_version || 'an unknown version' }}.
          </p>
          <p class="text-ink-gray-5 text-p-sm">
            Pilot updates itself and restarts the admin service. Your benches keep running.
          </p>
        </div>

        <p v-else class="py-4 text-ink-gray-5 text-sm text-center">You are on the latest version.</p>

        <ErrorMessage v-if="dialogError" :message="dialogError" class="mt-3" />
      </template>

      <template v-if="!isDev && updateAvailable" #actions>
        <Button variant="solid" class="w-full" :loading="updating" @click="update">
          Update to {{ latestVersion }}
        </Button>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Button, Dialog, ErrorMessage, Switch, toast } from 'frappe-ui'
import { cliUpdatesApi, settingsApi } from '@/api/settings'
import { tasksApi } from '@/api/tasks'
import { isTaskActive } from '@/utils/taskFormat'

const POLL_INTERVAL_MS = 1500

const loading = ref(true)
const checking = ref(false)
const updating = ref(false)
const status = ref({ current_version: '', is_dev: true })
const latestVersion = ref(null)
const log = ref('')
const error = ref(null)
const dialogOpen = ref(false)
const dialogError = ref(null)
const allowDeveloperMode = ref(false)
const savingDeveloperMode = ref(false)

const isDev = computed(() => status.value.is_dev || !status.value.current_version)
const versionLabel = computed(() => (isDev.value ? 'Development' : status.value.current_version))
const updateAvailable = computed(
  () => Boolean(latestVersion.value) && latestVersion.value !== status.value.current_version,
)

onMounted(async () => {
  const [versionResult, settingsResult] = await Promise.allSettled([
    cliUpdatesApi.status(),
    settingsApi.get(),
  ])
  if (versionResult.status === 'fulfilled') status.value = versionResult.value
  else error.value = 'Could not load version information.'
  if (settingsResult.status === 'fulfilled') {
    allowDeveloperMode.value = Boolean(settingsResult.value?.bench?.allow_developer_mode)
  }
  loading.value = false
})

async function toggleAllowDeveloperMode(value) {
  savingDeveloperMode.value = true
  error.value = null
  try {
    await settingsApi.update({ bench: { allow_developer_mode: value } })
    allowDeveloperMode.value = value
    toast.success(`Developer mode ${value ? 'allowed' : 'disallowed'}`)
  } catch (e) {
    error.value = e.message || 'Could not update developer mode setting.'
  } finally {
    savingDeveloperMode.value = false
  }
}

async function check() {
  if (checking.value) return
  dialogError.value = null
  log.value = ''
  if (isDev.value) {
    dialogOpen.value = true
    return
  }

  checking.value = true
  error.value = null
  try {
    const result = await cliUpdatesApi.check()
    status.value = { ...status.value, ...result }
    latestVersion.value = result.latest_version
    dialogOpen.value = true
  } catch {
    error.value = 'Could not check for updates.'
  } finally {
    checking.value = false
  }
}

async function update() {
  if (updating.value) return
  updating.value = true
  dialogError.value = null
  log.value = 'Starting update...'
  try {
    const { task_id } = await tasksApi.run('update-cli')
    await pollTask(task_id)
  } catch {
    dialogError.value = 'Update failed. Check the Tasks view for details.'
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
        dialogError.value = 'Lost contact with the admin service after the update. Check the Tasks view.'
        return
      }
      continue
    }
    log.value = (await tasksApi.output(taskId)) || log.value
    if (isTaskActive(task)) continue
    if (task.status !== 'success') {
      dialogError.value = 'Update did not complete successfully.'
      return
    }
    status.value = await cliUpdatesApi.status().catch(() => status.value)
    latestVersion.value = null
    dialogOpen.value = false
    toast.success('Updated successfully')
    return
  }
}
</script>
