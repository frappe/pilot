<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Button, Dialog, ErrorMessage, Spinner, toast } from 'frappe-ui'

import CopyBtn from '@/components/common/CopyBtn.vue'
import SettingsRow from '@/components/settings/SettingsRow.vue'

import { cliUpdatesApi } from '@/api/settings'
import { tasksApi } from '@/api/tasks'
import { isTaskActive } from '@/utils/taskFormat'

const DEV_COMMANDS = 'git pull\npilot admin build\npilot admin upgrade'

const POLL_INTERVAL_MS = 1500

const loading = ref(true)
const status = ref({ current_version: '', is_dev: true })
const latestVersion = ref(null)
const checking = ref(false)
const updating = ref(false)
const log = ref('')
const versionError = ref(null)
const dialogError = ref(null)
const dialogOpen = ref(false)

const isDev = computed(() => status.value.is_dev || !status.value.current_version)
const versionLabel = computed(() => (isDev.value ? 'Development' : status.value.current_version))
const updateAvailable = computed(
  () => Boolean(latestVersion.value) && latestVersion.value !== status.value.current_version,
)

onMounted(async () => {
  try {
    status.value = await cliUpdatesApi.status()
  } catch {
    versionError.value = 'Could not load version information.'
  } finally {
    loading.value = false
  }
})

const check = async () => {
  if (checking.value) return
  dialogError.value = null
  log.value = ''
  if (isDev.value) {
    dialogOpen.value = true
    return
  }

  checking.value = true
  versionError.value = null
  try {
    const result = await cliUpdatesApi.check()
    status.value = { ...status.value, ...result }
    latestVersion.value = result.latest_version
    if (result.latest_version && result.latest_version !== status.value.current_version) {
      dialogOpen.value = true
    } else {
      toast.info(`${status.value.current_version} (latest)`, {
        description: "You're on the latest version.",
        duration: 5000,
      })
    }
  } catch {
    versionError.value = 'Could not check for updates.'
  } finally {
    checking.value = false
  }
}

const update = async () => {
  if (updating.value) return
  updating.value = true
  dialogError.value = null
  log.value = ''
  try {
    const { task_id } = await tasksApi.run('update-cli')
    await pollTask(task_id)
  } catch {
    dialogError.value = 'Update failed. Check the Tasks view for details.'
  } finally {
    updating.value = false
  }
}

const pollTask = async (taskId) => {
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
        dialogError.value =
          'Lost contact with the admin service after the update. Check the Tasks view.'
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

<template>
  <SettingsRow label="Pilot Version" :description="loading ? '' : versionLabel">
    <Button :loading="checking" @click="check">Update</Button>
  </SettingsRow>

  <ErrorMessage v-if="versionError" :message="versionError" />

  <Dialog v-model="dialogOpen" title="Update" size="md">
    <div v-if="isDev" class="flex flex-col gap-3">
      <p class="text-ink-gray-7 text-p-base">
        This is a development install. Update it from a terminal:
      </p>

      <div class="relative">
        <pre
          class="bg-surface-gray-2 p-3 pr-10 rounded-4 overflow-x-auto font-mono text-ink-gray-8 text-sm leading-relaxed"
        >{{ DEV_COMMANDS }}</pre>

        <CopyBtn
          :text="DEV_COMMANDS"
          class="top-2 right-2 absolute text-ink-gray-5 hover:text-ink-gray-8 transition-colors"
        />
      </div>
      <p class="text-ink-gray-5 text-p-sm">The last step restarts the admin service.</p>
    </div>

    <div v-else-if="updating" class="flex flex-col gap-3">
      <p class="text-ink-gray-7">Updating to {{ latestVersion }}…</p>
      <div v-if="!log" class="flex justify-center items-center py-8">
        <Spinner size="lg" class="text-ink-gray-4" />
      </div>

      <pre
        v-else
        class="p-3 bg-surface-gray-2 rounded-4 max-h-64 overflow-auto text-ink-gray-7 text-sm whitespace-pre-wrap"
      >{{ log }}</pre>
    </div>

    <div v-else-if="updateAvailable" class="flex flex-col gap-3">
      <p class="text-ink-gray-7 text-p-base">
        Version <strong>{{ latestVersion }}</strong> is available. You are on
        {{ status.current_version || 'an unknown version' }}.
      </p>

      <p class="text-ink-gray-5 text-p-sm">
        Pilot updates itself and restarts the admin service. Your benches keep running.
      </p>
    </div>

    <ErrorMessage v-if="dialogError" :message="dialogError" class="mt-3" />

    <template v-if="!isDev && updateAvailable" #actions>
      <Button variant="solid" class="w-full" :loading="updating" @click="update">
        Update to {{ latestVersion }}
      </Button>
    </template>
  </Dialog>
</template>
