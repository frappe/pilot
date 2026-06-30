import ky from 'ky'

export const request = ky.create({
  prefixUrl: '/api',
})
