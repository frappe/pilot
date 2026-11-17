import { body, request } from '@/api/client'
import type { Snapshot } from '@/stores/editor'

export const sessionApi = {
  get: () =>
    request
      .get('state')
      .then((r) => body<Partial<Snapshot>>(r, {}))
      .catch((): Partial<Snapshot> => ({})),
  // Fire-and-forget; keepalive lets it survive an unload.
  put: (state: Snapshot) => request.put('state', { json: state, keepalive: true }).catch(() => {}),
}
