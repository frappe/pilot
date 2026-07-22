import { computed, ref } from 'vue'
import { appsApi } from '@/api/apps'
import { tasksApi } from '@/api/tasks'
import { isTaskActive } from '@/utils/taskFormat'

const updates = ref({})
const checking = ref(false)
const checked = ref(false)

const POLL_INTERVAL_MS = 1500

export function useAppUpdates() {
  async function check() {
    if (checking.value) return

    checking.value = true
    try {
      const { task_id } = await appsApi.fetchUpdates()
      while (true) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
        const task = await tasksApi.detail(task_id)

        if (isTaskActive(task)) continue
        if (task.status === 'success') {
          const output = await tasksApi.output(task_id)
          const lastLine = output.trimEnd().split(/\r?\n/).at(-1)
          if (lastLine) updates.value = JSON.parse(lastLine)
        }
        break
      }
    } catch {
      // Best-effort update check; failures should not block the UI.
    } finally {
      checking.value = false
      checked.value = true
    }
  }

  const appsWithUpdates = computed(() => Object.keys(updates.value).filter((name) => updates.value[name]))
  const updatesAvailable = computed(() => appsWithUpdates.value.length > 0)

  return {
    updates,
    updatesAvailable,
    appsWithUpdates,
    checking,
    checked,
    check,
  }
}
