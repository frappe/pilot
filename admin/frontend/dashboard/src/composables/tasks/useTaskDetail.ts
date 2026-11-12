import { ref, toValue } from 'vue'
import type { MaybeRefOrGetter } from 'vue'

import { tasksApi } from '@/api/tasks'
import type { TaskPayload } from '@/types/tasks'
import { isTaskActive } from '@/utils/taskFormat'

export const useTaskDetail = (taskId: MaybeRefOrGetter<string>) => {
  const task = ref<TaskPayload | null>(null)
  const rawLines = ref<string[]>([])
  const loading = ref(false)
  const error = ref('')

  const load = async () => {
    loading.value = true
    error.value = ''
    try {
      task.value = await tasksApi.detail(toValue(taskId))
      rawLines.value = []
      if (!isTaskActive(task.value)) {
        const output = await tasksApi.output(toValue(taskId))
        if (output) rawLines.value = output.replace(/\r?\n$/, '').split(/\r?\n/)
      }
    } catch (caught) {
      error.value = (caught instanceof Error && caught.message) || 'Failed to load task'
    } finally {
      loading.value = false
    }
  }

  return { task, rawLines, loading, error, load }
}
