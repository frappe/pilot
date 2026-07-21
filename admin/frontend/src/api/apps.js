import { request, unwrap } from './client'

export const appsApi = {
  marketplace: () => unwrap(request.get('marketplace/apps').json()),
  installed: () => unwrap(request.get('apps').json()),
  fetchUpdates: () => request.post('apps/fetch').json(),
  add: (payload) => request.post('apps', { json: payload }).json(),
}
