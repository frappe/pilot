import { ref } from 'vue'
import { tasksApi } from '@/api/tasks'

const HIDDEN_COMMANDS = new Set(['fetch-all-app-updates'])

const tasks = ref([])
const loading = ref(false)
const error = ref('')

export function useTasks() {
  async function load(status = 'all') {
    loading.value = true
    error.value = ''
    try {
      const list = await tasksApi.list(status)
      tasks.value = list.filter((task) => !HIDDEN_COMMANDS.has(task.command))
    } catch (caught) {
      error.value = caught.message || 'Failed to load tasks'
      tasks.value = []
    } finally {
      loading.value = false
    }
  }

  return { tasks, loading, error, load }
}
