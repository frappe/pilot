import { request } from './client'

export const migrationsApi = {
  list: (params = {}) => request.get('migrations', { searchParams: params }).json(),
  current: () => request.get('migrations/current').json(),
  detail: (id) => request.get(`migrations/${id}`).json(),
  createUpdate: (json = {}) => request.post('updates', { json }).json(),
  retry: (id) => request.post(`migrations/${id}/actions/retry`).json(),
  restore: (id) => request.post(`migrations/${id}/actions/restore`).json(),
  bypassPatch: (id, patch) =>
    request.post(`migrations/${id}/actions/bypass-patch`, { json: { patch } }).json(),
}

export const ACTIVE_STATES = [
  'preparing',
  'backing_up',
  'updating',
  'migrating',
  'retrying',
  'reverting_apps',
  'reverting_sites',
  'restarting',
]
export const ATTENTION_STATES = ['needs_attention', 'revert_failed']

export function isResolved(operation) {
  return !operation || operation.state === 'completed' || operation.state === 'reverted'
}

export function needsAttention(operation) {
  return !!operation && ATTENTION_STATES.includes(operation.state)
}

export function isActive(operation) {
  return !!operation && ACTIVE_STATES.includes(operation.state)
}
