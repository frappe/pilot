import { request } from './client'

export const sshKeysApi = {
  list: () => request.get('ssh-keys').json(),
  add: (public_key) => request.post('ssh-keys', { json: { public_key } }).json(),
  remove: (fingerprint) => request.delete(`ssh-keys/${encodeURIComponent(fingerprint)}`),
}
