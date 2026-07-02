import { request } from './client'

export const benchesApi = {
  list: () => request.get('benches/').json(),
  control: (name, action) => request.post(`benches/${encodeURIComponent(name)}/actions/${action}`).json(),
  drop: (name) => request.delete(`benches/${encodeURIComponent(name)}`).json(),
  create: (payload) => request.post('benches/new', { json: payload }).json(),
  wildcardDomains: () => request.get('benches/wildcard-domains').json(),
  ready: (searchParams) => request.get('benches/ready', { searchParams }).json(),
}
