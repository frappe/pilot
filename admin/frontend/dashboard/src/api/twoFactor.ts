import { request, unwrap } from '@/api/client'
import type { RecoveryCodes, TwoFactorEnrollment, TwoFactorStatus } from '@/types/auth'

const path = (name: string) => `auth/two-factor/${encodeURIComponent(name)}`

export const twoFactorApi = {
  status: (): Promise<TwoFactorStatus> => unwrap(request.get('auth/two-factor').json()),

  startEnrollment: (name: string): Promise<TwoFactorEnrollment> =>
    unwrap(request.post('auth/two-factor/enrollment', { json: { name } }).json()),

  confirm: (name: string, otp: string): Promise<TwoFactorStatus> =>
    unwrap(request.post(path(name), { json: { otp } }).json()),

  removeDevice: (name: string): Promise<TwoFactorStatus> =>
    unwrap(request.delete(path(name)).json()),

  regenerateRecoveryCodes: (): Promise<RecoveryCodes> =>
    unwrap(request.post('auth/two-factor/recovery-codes').json()),
}
