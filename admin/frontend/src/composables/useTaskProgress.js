import { ref } from 'vue'

const activeTaskId = ref(null)

export function useTaskProgress() {
  function watchTask(taskId) {
    activeTaskId.value = taskId
  }

  function clearTask() {
    activeTaskId.value = null
  }

  return { activeTaskId, watchTask, clearTask }
}
