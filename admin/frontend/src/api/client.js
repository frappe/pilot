import ky from 'ky'

export const API_V1_PREFIX = '/api/v1'

export function apiUrl(path = '', origin = '') {
  const suffix = path ? `/${String(path).replace(/^\/+/, '')}` : ''
  return `${origin}${API_V1_PREFIX}${suffix}`
}

export const request = ky.create({
  prefix: API_V1_PREFIX,
  throwHttpErrors: false,
  // ky's default is 10s; some admin operations (git/mariadb checks) can
  // legitimately run longer than that, well under nginx/gunicorn's 120s ceiling.
  timeout: 60_000,
})
