import { request, body } from './client'

export const sessionApi = {
  get: () =>
    request
      .get('state')
      .then((r) => body(r, {}))
      .catch(() => ({})),
  // Fire-and-forget; keepalive lets it survive an unload.
  put: (state) => request.put('state', { json: state, keepalive: true }).catch(() => {}),
}
