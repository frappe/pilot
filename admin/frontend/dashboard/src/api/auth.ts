import { request } from '@/api/client'
import type { AdminSession } from '@/types/auth'
import type { Bootstrap } from '@/types/core'

export const authApi = {
  bootstrap: (): Promise<Bootstrap> => request.get('bootstrap').json(),
  session: (): Promise<AdminSession> => request.get('auth/session').json(),

  login: (password: string, otp?: string): Promise<AdminSession> =>
    request.post('auth/session', { json: { password, otp } }).json(),

  loginWithSid: (sid: string): Promise<AdminSession> =>
    request.post('auth/session', { json: { sid } }).json(),

  logout: () => request.delete('auth/session'),
}
