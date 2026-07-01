import { request } from './client'

export const settingsApi = {
  get: () => request.get('settings/').json(),
  update: (data) => request.patch('settings/', { json: data }).json(),
  myIp: () => request.get('settings/my-ip').json(),
}
