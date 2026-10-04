import type { Router } from 'vue-router'

export const taskDetailRoute = (taskId: string) => {
  return { name: 'TaskDetail', params: { taskId } }
}

export const openTaskDetailPage = (router: Router, taskId: string) => {
  router.push(taskDetailRoute(taskId))
}
