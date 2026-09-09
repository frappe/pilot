export const taskDetailRoute = (taskId) => {
  return { name: 'TaskDetail', params: { taskId } }
}

export const openTaskDetailPage = (router, taskId) => {
  router.push(taskDetailRoute(taskId))
}
