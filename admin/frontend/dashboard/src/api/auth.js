import { request } from './client'

export const authApi = {
  bootstrap: () => request.get('bootstrap').json(),
  session: () => request.get('session').json(),
  login: (password) => request.post('session', { json: { password } }).json(),
  loginWithSid: (sid) => request.post('session', { json: { sid } }).json(),
  logout: () => request.delete('session'),
}
