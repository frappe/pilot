import { request } from './client'

export const appsApi = {
  registry: () => request.get('apps/registry').json(),
  installed: () => request.get('apps/').json(),
  fetchUpdates: () => request.post('apps/fetch').json(),
  addAndInstall: (payload) => request.post('apps/add-and-install', { json: payload }).json(),
}
