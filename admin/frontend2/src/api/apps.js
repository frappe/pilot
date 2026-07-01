import { request } from './client'

export const appsApi = {
  marketplace: () => request.get('apps/marketplace').json(),
  installed: () => request.get('apps/').json(),
  fetchUpdates: () => request.post('apps/fetch').json(),
}
