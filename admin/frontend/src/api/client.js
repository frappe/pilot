import ky from 'ky'

export const request = ky.create({
  prefix: '/api',
  throwHttpErrors: false,
})
