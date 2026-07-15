import { apiUrl, request } from './client'

export const tasksApi = {
  list: (status) => request.get('tasks/', status && status !== 'all' ? { searchParams: { status } } : {}).json(),
  detail: (taskId) => request.get(`tasks/${taskId}`).json(),
  run: (command, args = {}) => request.post('tasks/run', { json: { command, ...args } }).json(),
  cancel: (taskId) => request.post(`tasks/${taskId}/kill`).json(),
  rerun: (taskId) => request.post(`tasks/${taskId}/rerun`).json(),
  streamUrl: (taskId) => apiUrl(`tasks/${taskId}/stream`),
}
