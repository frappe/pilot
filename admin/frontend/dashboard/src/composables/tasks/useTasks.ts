import { ref } from 'vue'

import { tasksApi } from '@/api/tasks'

const tasks = ref([])
const loading = ref(false)
const error = ref('')

export const useTasks = () => {
  const load = async (status = 'all') => {
    loading.value = true
    error.value = ''
    try {
      tasks.value = await tasksApi.list(status)
    } catch (caught) {
      error.value = caught.message || 'Failed to load tasks'
      tasks.value = []
    } finally {
      loading.value = false
    }
  }

  return { tasks, loading, error, load }
}
