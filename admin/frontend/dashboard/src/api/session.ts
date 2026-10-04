import { request, unwrap } from '@/api/client'
import type { ActiveSessions, RevokedSessions } from '@/types/auth'

export const sessionApi = {
  list: (): Promise<ActiveSessions> => unwrap(request.get('auth/sessions').json()),
  revoke: (jti: string) => request.post(`auth/sessions/revoke/${jti}`),

  revokeAll: (): Promise<RevokedSessions> =>
    unwrap(request.post('auth/sessions/revoke/all').json()),
}
