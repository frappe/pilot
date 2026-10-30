<script setup lang="ts">
import { Badge, Button, Dialog, ErrorMessage, Skeleton, Spinner, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { cliUpdatesApi } from '@/api/settings'
import { tasksApi } from '@/api/tasks'
import CopyBtn from '@/components/common/CopyBtn.vue'
import PilotLogo from '@/components/icons/Pilot.vue'
import type { TaskPayload } from '@/types/tasks'
import { isTaskActive } from '@/utils/taskFormat'

defineProps<{ platform?: string }>()

const DEV_COMMANDS = 'git pull\npilot admin build\npilot admin upgrade'

const POLL_INTERVAL_MS = 1500

const loading = ref(true)
const status = ref<{ current_version: string; is_dev: boolean; restarts_admin?: boolean }>({
  current_version: '',
  is_dev: true,
})
const latestVersion = ref<string | null>(null)
const checking = ref(false)
const updating = ref(false)
const log = ref('')
const versionError = ref<string | null>(null)
const dialogError = ref<string | null>(null)
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
    const latest = 'latest_version' in result ? result.latest_version : null

    status.value = { ...status.value, ...result }
    latestVersion.value = latest
    if (latest && latest !== status.value.current_version) {
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

const pollTask = async (taskId: string) => {
  // The admin service restarts mid-update, so detail requests fail transiently.
  // Give it a bounded window to come back before declaring the update lost.
  const MAX_CONSECUTIVE_FAILURES = 40 // ~60s at POLL_INTERVAL_MS
  let failures = 0
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    let task: TaskPayload
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
    const restartsAdmin = status.value.restarts_admin !== false
    status.value = await cliUpdatesApi.status().catch(() => status.value)
    latestVersion.value = null
    dialogOpen.value = false
    toast.success('Updated successfully', {
      description: restartsAdmin
        ? undefined
        : 'Stop pilot start and run it again to use the new version.',
    })
    return
  }
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-3.5">
    <PilotLogo class="size-11 shrink-0" />

    <div class="flex flex-col flex-1 gap-1.5 min-w-0">
      <div class="flex items-center gap-2">
        <p class="text-lg-semibold text-ink-gray-8">Pilot</p>
        <Badge v-if="!loading" :label="versionLabel" />
      </div>

      <p v-if="platform" class="text-ink-gray-5 text-sm truncate">Running on {{ platform }}</p>
      <Skeleton v-else class="rounded-1 w-44 h-3" />
    </div>

    <Button class="max-sm:w-full shrink-0" label="Update" :loading="checking" @click="check" />
  </div>

  <ErrorMessage v-if="versionError" :message="versionError" />

  <Dialog
    v-model="dialogOpen"
    :title="isDev ? 'Update from a terminal' : updating ? `Updating to ${latestVersion}` : 'Update available'"
    icon="lucide-arrow-up"
    size="md"
  >
    <p class="mb-4 text-ink-gray-6 text-p-sm">
      <template v-if="isDev">
        This is a development install. The last step restarts the admin service.
      </template>
      <template v-else-if="updating">This takes a minute. Keep this window open.</template>
      <template v-else-if="status.restarts_admin !== false">
        Pilot updates itself and restarts the admin service. Your benches keep running.
      </template>
      <template v-else>
        Pilot updates itself. Then stop <code>pilot start</code> and run it again.
      </template>
    </p>

    <div
      v-if="isDev"
      class="relative bg-surface-gray-1 py-2.5 border border-outline-gray-1 rounded-6"
    >
      <p
        v-for="command in DEV_COMMANDS.split('\n')"
        :key="command"
        class="before:mr-2.5 px-3.5 py-1 font-mono text-ink-gray-8 text-sm before:text-ink-gray-4 before:content-['$']"
      >
        {{ command }}
      </p>

      <CopyBtn
        :text="DEV_COMMANDS.replaceAll('\n', ' && ')"
        class="top-3 right-3 absolute text-ink-gray-5 hover:text-ink-gray-8 transition-colors"
      />
    </div>

    <template v-else-if="updating">
      <div v-if="!log" class="flex justify-center items-center py-6">
        <Spinner size="lg" class="text-ink-gray-4" />
      </div>

      <pre
        v-else
        class="bg-surface-gray-1 p-3 border border-outline-gray-1 rounded-6 max-h-64 overflow-auto font-mono text-ink-gray-7 text-sm whitespace-pre-wrap"
      >{{ log }}</pre>
    </template>

    <div v-else-if="updateAvailable" class="flex justify-center items-center gap-2">
      <Badge :label="status.current_version || 'Unknown'" size="lg" />
      <span class="size-4 text-ink-gray-4 lucide-arrow-right" aria-hidden="true" />
      <Badge :label="latestVersion ?? ''" theme="green" size="lg" />
    </div>

    <ErrorMessage v-if="dialogError" :message="dialogError" class="mt-3" />

    <template v-if="!isDev && updateAvailable" #actions>
      <Button variant="solid" class="w-full" :loading="updating" @click="update">
        Update to {{ latestVersion }}
      </Button>
    </template>
  </Dialog>
</template>
