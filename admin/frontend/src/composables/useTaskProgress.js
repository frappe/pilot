import { useRouter } from 'vue-router'

export function useTaskProgress() {
  const router = useRouter()

  function watchTask(taskId) {
    router.push(`/tasks/${taskId}`)
  }

  return { watchTask }
}
