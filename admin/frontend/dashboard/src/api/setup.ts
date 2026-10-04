import { apiUrl, request } from '@/api/client'
import type { Bootstrap } from '@/types/core'
import type { DatabaseValidation, FrameworkBranches, SetupConfiguration } from '@/types/setup'
import type { TaskPayload } from '@/types/tasks'

const setupIdempotencyKey = 'wizard-setup'

export const setupApi = {
  bootstrap: (): Promise<Bootstrap> => request.get('bootstrap').json(),
  config: (): Promise<SetupConfiguration> => request.get('setup/configuration').json(),
  branches: (): Promise<FrameworkBranches> => request.get('setup/framework-branches').json(),

  validateDatabase: (json: Record<string, unknown>): Promise<DatabaseValidation> =>
    request.post('setup/database-validations', { json }).json(),

  save: (json: Record<string, unknown>): Promise<SetupConfiguration> =>
    request.put('setup/configuration', { json }).json(),

  start: (): Promise<TaskPayload> =>
    request
      .post('setup/actions/start', {
        headers: { 'Idempotency-Key': setupIdempotencyKey },
      })
      .json(),

  finish: (taskId: string) => request.post('setup/actions/finish', { json: { task_id: taskId } }),
  streamUrl: (taskId: string) => apiUrl(`tasks/${taskId}/events`),
}
