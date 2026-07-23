<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <div>
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">Version</p>
      <div class="divide-y divide-outline-gray-1">
        <div class="flex justify-between items-center py-2.5">
          <span class="text-ink-gray-7 text-sm">Current version</span>
          <span class="flex items-center gap-2 text-ink-gray-9 text-sm">
            {{ status.current_version }}
            <span v-if="status.is_dev"
              class="px-1.5 py-0.5 rounded bg-surface-gray-3 text-ink-gray-6 text-xs">dev</span>
          </span>
        </div>
        <div v-if="status.is_dev && status.branch" class="flex justify-between items-center py-2.5">
          <span class="text-ink-gray-7 text-sm">Branch</span>
          <span class="text-ink-gray-9 text-sm">{{ status.branch }}</span>
        </div>
      </div>
    </div>

    <div v-if="status.is_dev">
      <p class="text-ink-gray-5 text-sm">
        This is a development install. Update it with <code
          class="px-1 bg-surface-gray-2 rounded text-ink-gray-7">git pull</code> or
        <code class="px-1 bg-surface-gray-2 rounded text-ink-gray-7">bench upgrade</code>.
      </p>
    </div>

    <div v-else class="space-y-3">
      <div class="flex items-center gap-3">
        <Button :loading="checking" icon-left="lucide-refresh-cw" @click="check">Check for updates</Button>
        <Button v-if="latestVersion && updateAvailable" variant="solid" :loading="updating"
          icon-left="lucide-download" @click="update">
          Update to {{ latestVersion }}
        </Button>
      </div>
      <p v-if="checked && !updateAvailable && !updating" class="text-ink-gray-6 text-sm">
        You are on the latest version.
      </p>
      <p v-else-if="updateAvailable && !updating" class="text-ink-gray-7 text-sm">
        A new version is available: {{ latestVersion }}
      </p>

      <pre v-if="log" class="p-3 bg-surface-gray-2 rounded max-h-48 overflow-auto text-ink-gray-7 text-xs whitespace-pre-wrap">{{ log }}</pre>
      <ErrorMessage :message="error" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Button, ErrorMessage } from 'frappe-ui'
import { cliUpdatesApi } from '@/api/settings'
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

const updateAvailable = computed(() => Boolean(latestVersion.value) && latestVersion.value !== status.value.current_version)

onMounted(async () => {
  try {
    status.value = await cliUpdatesApi.status()
  } catch {
    error.value = 'Could not load version information.'
  } finally {
    loading.value = false
  }
})

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
