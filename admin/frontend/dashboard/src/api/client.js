import ky from 'ky'

export const API_V1_PREFIX = '/api/v1'

export function apiUrl(path = '', origin = '') {
  const suffix = path ? `/${String(path).replace(/^\/+/, '')}` : ''
  return `${origin}${API_V1_PREFIX}${suffix}`
}

export function apiErrorMessage(payload, fallback = 'Request failed.') {
  const error = payload?.error
  if (typeof error?.message === 'string' && error.message) return error.message
  if (typeof error === 'string' && error) return error
  return fallback
}

export async function unwrap(parsed) {
  const data = await parsed
  if (data?.error) throw new Error(apiErrorMessage(data))
  return data
}

export const request = ky.create({
  prefix: API_V1_PREFIX,
  throwHttpErrors: false,
  // ky's default is 10s; some admin operations (git/mariadb checks) can
  // legitimately run longer than that, well under nginx/gunicorn's 120s ceiling.
  timeout: 60_000,
})
