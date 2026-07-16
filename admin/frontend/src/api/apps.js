import { request } from './client'

// ky is configured with throwHttpErrors: false, so a 500 response (e.g.
// {"error": "..."}) resolves instead of rejecting — callers expecting an
// array would otherwise silently get an error object. Throw here instead,
// once, so every caller gets the real backend message.
async function getOrThrow(path) {
  const data = await request.get(path).json()
  if (data?.error) throw new Error(data.error)
  return data
}

export const appsApi = {
  marketplace: () => getOrThrow('apps/marketplace'),
  installed: () => getOrThrow('apps/'),
  fetchUpdates: () => request.post('apps/fetch').json(),
  add: (payload) => request.post('apps/add', { json: payload }).json(),
}
