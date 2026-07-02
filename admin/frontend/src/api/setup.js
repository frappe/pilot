import { request } from './client'

export const setupApi = {
  config: () => request.get('setup/config').json(),
  branches: () => request.get('setup/branches').json(),
  status: () => request.get('status'),
  validateMariadb: (json) => request.post('setup/validate-mariadb', { json }).json(),
  validatePostgres: (json) => request.post('setup/validate-postgres', { json }).json(),
  save: (json) => request.post('setup/save', { json }).json(),
  start: () => request.post('setup/start').json(),
  finish: () => request.post('setup/finish').json(),
  streamUrl: (taskId) => `/api/setup/stream/${taskId}`,
}
