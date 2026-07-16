import { request } from './client'

export const benchesApi = {
  list: () => request.get('benches').json(),
  start: (name) => request.post(`benches/${encodeURIComponent(name)}/actions/start`).json(),
  stop: (name) => request.post(`benches/${encodeURIComponent(name)}/actions/stop`).json(),
  restart: (name) => request.post(`benches/${encodeURIComponent(name)}/actions/restart`).json(),
  drop: (name) => request.delete(`benches/${encodeURIComponent(name)}`),
  create: (payload) => request.post('benches', { json: payload }).json(),
  wildcardDomains: () => request.get('benches/domain-options').json(),
  ready: (payload) => request.post('bench-readiness-checks', { json: payload }).json(),
}
