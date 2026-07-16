import { ref } from 'vue'
import { tasksApi } from '@/api/tasks'
import { isTaskActive } from '@/utils/taskFormat'

export function useTaskDetail(taskId) {
  const task = ref(null)
  const rawLines = ref([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      task.value = await tasksApi.detail(taskId)
      rawLines.value = []
      if (!isTaskActive(task.value)) {
        const output = await tasksApi.output(taskId)
        if (output) rawLines.value = output.replace(/\r?\n$/, '').split(/\r?\n/)
      }
    } catch (caught) {
      error.value = caught.message || 'Failed to load task'
    } finally {
      loading.value = false
    }
  }

  return { task, rawLines, loading, error, load }
}
