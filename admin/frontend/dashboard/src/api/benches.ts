import { request } from '@/api/client'
import type { BenchReadiness, BenchResource, CreatedBench } from '@/types/benches'
import type { WildcardDomains } from '@/types/sites'

export const benchesApi = {
  list: (): Promise<BenchResource[]> => request.get('benches').json(),

  start: (name: string): Promise<BenchResource> =>
    request.post(`benches/${encodeURIComponent(name)}/actions/start`).json(),

  stop: (name: string): Promise<BenchResource> =>
    request.post(`benches/${encodeURIComponent(name)}/actions/stop`).json(),

  restart: (name: string): Promise<BenchResource> =>
    request.post(`benches/${encodeURIComponent(name)}/actions/restart`).json(),

  drop: (name: string) => request.delete(`benches/${encodeURIComponent(name)}`),

  create: (payload: Record<string, unknown>): Promise<CreatedBench> =>
    request.post('benches', { json: payload }).json(),

  wildcardDomains: (): Promise<WildcardDomains> => request.get('benches/domain-options').json(),

  ready: (payload: Record<string, unknown>): Promise<BenchReadiness> =>
    request.post('bench-readiness-checks', { json: payload }).json(),
}
