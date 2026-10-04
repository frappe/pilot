import { request } from '@/api/client'
import type { AuthorizedSSHKey, AuthorizedSSHKeys } from '@/types/sshKeys'

export const sshKeysApi = {
  list: (): Promise<AuthorizedSSHKeys> => request.get('ssh-keys').json(),

  add: (public_key: string): Promise<AuthorizedSSHKey> =>
    request.post('ssh-keys', { json: { public_key } }).json(),

  remove: (fingerprint: string) => request.delete(`ssh-keys/${encodeURIComponent(fingerprint)}`),
}
