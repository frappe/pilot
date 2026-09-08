import { request, unwrap } from '@/api/client'

export const processesApi = {
  list: () => unwrap(request.get('processes').json()),
  restart: (name) => unwrap(request.post(`processes/${encodeURIComponent(name)}/actions/restart`).json()),
  restartWorkload: () => unwrap(request.post('processes/actions/restart').json()),
}
