import ky from 'ky'

export const request = ky.create({
  prefix: '/api',
  throwHttpErrors: false,
  // ky's default is 10s; some admin operations (git/mariadb checks) can
  // legitimately run longer than that, well under nginx/gunicorn's 120s ceiling.
  timeout: 60_000,
})
