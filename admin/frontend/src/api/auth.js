import { request } from './client'

export const authApi = {
  status: () => request.get('status').json(),
  login: (password) => request.post('login', { json: { password } }).json(),
  loginWithSid: (sid) => request.post('login', { json: { sid } }).json(),
  logout: () => request.post('logout'),
}
