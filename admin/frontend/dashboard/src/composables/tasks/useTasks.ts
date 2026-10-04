import { ref } from 'vue'

import { tasksApi } from '@/api/tasks'
import type { TaskPayload } from '@/types/tasks'

const tasks = ref<TaskPayload[]>([])
const loading = ref(false)
const error = ref('')

export const useTasks = () => {
  const load = async (status = 'all') => {
    loading.value = true
    error.value = ''
    try {
      tasks.value = await tasksApi.list(status)
    } catch (caught) {
      error.value = (caught instanceof Error && caught.message) || 'Failed to load tasks'
      tasks.value = []
    } finally {
      loading.value = false
    }
  }

  return { tasks, loading, error, load }
}
