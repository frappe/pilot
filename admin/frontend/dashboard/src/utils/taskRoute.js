export function taskDetailRoute(taskId) {
  return { name: 'TaskDetail', params: { taskId } }
}

export function openTaskDetailPage(router, taskId) {
  router.push(taskDetailRoute(taskId))
}
