import { request } from '@/api/client'
import type { MigrationAccepted, MigrationPage, MigrationSummary } from '@/types/migrations'
import { ACTIVE_STATES, ATTENTION_STATES } from '@/utils/updateFormat.ts'

type Operation = MigrationSummary | null | undefined

export const updatesApi = {
  list: (params: Record<string, string | number> = {}): Promise<MigrationPage> =>
    request.get('migrations', { searchParams: params }).json(),

  current: (): Promise<MigrationSummary | null> => request.get('migrations/current').json(),
  detail: (id: string): Promise<MigrationSummary> => request.get(`migrations/${id}`).json(),

  createUpdate: (json: Record<string, unknown> = {}): Promise<MigrationAccepted> =>
    request.post('updates', { json }).json(),

  retry: (id: string): Promise<MigrationAccepted> =>
    request.post(`migrations/${id}/actions/retry`).json(),

  restore: (id: string): Promise<MigrationAccepted> =>
    request.post(`migrations/${id}/actions/restore`).json(),

  bypassPatch: (id: string, patch: string): Promise<MigrationAccepted> =>
    request.post(`migrations/${id}/actions/bypass-patch`, { json: { patch } }).json(),
}

export const isResolved = (operation: Operation) => {
  return !operation || operation.state === 'completed' || operation.state === 'reverted'
}

export const needsAttention = (operation: Operation): boolean => {
  return !!operation && ATTENTION_STATES.includes(operation.state)
}

export const isActive = (operation: Operation): boolean => {
  return !!operation && ACTIVE_STATES.includes(operation.state)
}

export const isPending = (operation: Operation): boolean => {
  return !!operation?.pending_action
}
