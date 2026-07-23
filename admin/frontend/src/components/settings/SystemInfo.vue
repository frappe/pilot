<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <div>
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">Version</p>
      <div class="flex justify-between items-center gap-3 py-2.5">
        <span class="text-ink-gray-9 text-sm">{{ versionLabel }}</span>
        <Button variant="subtle" :loading="checking" @click="check">Check updates</Button>
      </div>
      <ErrorMessage v-if="versionError" :message="versionError" />
    </div>

    <div>
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">System</p>
      <div class="divide-y divide-outline-gray-1">
        <div v-for="(value, label) in systemRows" :key="label" class="flex justify-between items-center py-2.5">
          <span class="text-ink-gray-7 text-sm">{{ label }}</span>
          <span class="text-ink-gray-9 text-sm">{{ value }}</span>
        </div>
      </div>
    </div>

    <div>
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">Runtime</p>
      <div class="divide-y divide-outline-gray-1">
        <div v-for="(value, label) in info.runtime" :key="label" class="flex justify-between items-center py-2.5">
          <span class="text-ink-gray-7 text-sm">{{ label }}</span>
          <span class="text-ink-gray-9 text-sm">{{ value }}</span>
        </div>
        <p v-if="!Object.keys(info.runtime).length" class="py-2.5 text-ink-gray-5 text-sm">
          No runtime versions detected.
        </p>
      </div>
    </div>

    <Dialog v-model="dialogOpen" :options="{ title: 'Update', size: 'md' }">
      <div v-if="isDev" class="flex flex-col gap-3">
        <p class="text-ink-gray-7 text-sm">This is a development install. Update it from a terminal:</p>
        <pre class="p-3 bg-surface-gray-2 rounded overflow-x-auto text-ink-gray-8 text-xs">git pull
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
import { Button, Dialog, ErrorMessage, toast } from 'frappe-ui'
import { monitorApi } from '@/api/monitor'
import { cliUpdatesApi } from '@/api/settings'
import { tasksApi } from '@/api/tasks'
import { formatBytes } from '@/utils/format'
import { isTaskActive } from '@/utils/taskFormat'

const POLL_INTERVAL_MS = 1500

const loading = ref(true)
const info = ref({ disk_total: 0, runtime: {} })
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

const systemRows = computed(() => {
  const rows = {
    OS: info.value.os_version || '',
    Kernel: info.value.kernel_version || '',
    vCPUs: info.value.cpu_count || '',
    RAM: info.value.memory_total ? formatBytes(info.value.memory_total) : '',
    Swap: info.value.swap_total ? formatBytes(info.value.swap_total) : '',
    'Disk size': info.value.disk_total ? formatBytes(info.value.disk_total) : '',
  }
  return Object.fromEntries(Object.entries(rows).filter(([, value]) => value))
})

onMounted(async () => {
  const [systemResult, versionResult] = await Promise.allSettled([
    monitorApi.systemInfo(),
    cliUpdatesApi.status(),
  ])
  if (systemResult.status === 'fulfilled') info.value = systemResult.value
  if (versionResult.status === 'fulfilled') status.value = versionResult.value
  else versionError.value = 'Could not load version information.'
  loading.value = false
})

async function check() {
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
    dialogOpen.value = true
  } catch {
    versionError.value = 'Could not check for updates.'
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
