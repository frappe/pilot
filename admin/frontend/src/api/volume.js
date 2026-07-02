import { request } from './client'

export const volumeApi = {
  status: () => request.get('volume/status').json(),
  snapshots: {
    list: () => request.get('volume/snapshots').json(),
    create: () => request.post('volume/snapshots').json(),
    rollback: (tag) => request.post(`volume/snapshots/${encodeURIComponent(tag)}/rollback`).json(),
    destroy: (tag) => request.delete(`volume/snapshots/${encodeURIComponent(tag)}`).json(),
    schedule: {
      get: () => request.get('volume/snapshot-schedule').json(),
      set: (schedule) => request.post('volume/snapshot-schedule', { json: { schedule } }).json(),
      remove: () => request.delete('volume/snapshot-schedule').json(),
    },
  },
}
