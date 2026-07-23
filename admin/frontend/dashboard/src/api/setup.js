import { apiUrl, request } from './client'

const SETUP_IDEMPOTENCY_KEY = 'wizard-setup'

export const setupApi = {
  bootstrap: () => request.get('bootstrap').json(),
  config: () => request.get('setup/configuration').json(),
  branches: () => request.get('setup/framework-branches').json(),
  validateDatabase: (json) => request.post('setup/database-validations', { json }).json(),
  save: (json) => request.put('setup/configuration', { json }).json(),
  start: () =>
    request
      .post('setup/actions/start', {
        headers: { 'Idempotency-Key': SETUP_IDEMPOTENCY_KEY },
      })
      .json(),
  finish: (taskId) => request.post('setup/actions/finish', { json: { task_id: taskId } }),
  streamUrl: (taskId) => apiUrl(`tasks/${taskId}/events`),
}
