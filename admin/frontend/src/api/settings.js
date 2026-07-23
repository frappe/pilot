import { request } from './client'

export const settingsApi = {
  get: () => request.get('settings').json(),
  update: (data) => request.patch('settings', { json: data }).json(),
  myIp: () => request.get('network/client').json(),
}

export const cliUpdatesApi = {
  status: () => request.get('cli-updates').json(),
  check: () => request.post('cli-update-checks').json(),
}
