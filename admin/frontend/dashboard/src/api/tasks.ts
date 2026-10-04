import { apiUrl, request } from '@/api/client'
import type { TaskPayload, TaskWorker } from '@/types/tasks'

export const tasksApi = {
  list: (status?: string): Promise<TaskPayload[]> =>
    request.get('tasks', status && status !== 'all' ? { searchParams: { status } } : {}).json(),

  detail: (taskId: string): Promise<TaskPayload> => request.get(`tasks/${taskId}`).json(),

  run: (command: string, args: Record<string, unknown> = {}): Promise<TaskPayload> =>
    request.post('tasks', { json: { command, ...args } }).json(),

  cancel: (taskId: string) => request.delete(`tasks/${taskId}`),

  retry: (taskId: string): Promise<TaskPayload> =>
    request.post(`tasks/${taskId}/actions/retry`).json(),

  output: async (taskId: string) => {
    const response = await request.get(`tasks/${taskId}/output/content`)
    return response.ok ? response.text() : ''
  },

  outputUrl: (taskId: string) => apiUrl(`tasks/${taskId}/output/content`),
  streamUrl: (taskId: string) => apiUrl(`tasks/${taskId}/events`),

  debugUrl: (taskId: string, refresh = false) =>
    apiUrl(`tasks/${taskId}/debug${refresh ? '?refresh=1' : ''}`),
}

export const taskWorkerApi = {
  detail: (): Promise<TaskWorker> => request.get('task-worker').json(),
  start: (): Promise<TaskWorker> => request.post('task-worker/actions/start').json(),
  stop: (): Promise<TaskWorker> => request.post('task-worker/actions/stop').json(),
}
