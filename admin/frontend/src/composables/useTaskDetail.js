import { ref } from 'vue'
import { tasksApi } from '@/api/tasks'

export function useTaskDetail(taskId) {
  const task = ref(null)
  const rawLines = ref([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const data = await tasksApi.detail(taskId)
      task.value = data.task
      rawLines.value = data.output
    } catch (caught) {
      error.value = caught.message || 'Failed to load task'
    } finally {
      loading.value = false
    }
  }

  return { task, rawLines, loading, error, load }
}
