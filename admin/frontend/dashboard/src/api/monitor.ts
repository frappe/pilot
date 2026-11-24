import { request, unwrap } from '@/api/client'
import type {
  DatabaseHistory,
  SystemHistory,
  SystemInfo,
  SystemMetrics,
  WafAnalytics,
} from '@/types/stats'
import type { StorageBreakdown } from '@/types/storage'
import type { TaskPayload } from '@/types/tasks'

export const monitorApi = {
  stats: (): Promise<SystemMetrics> => request.get('metrics').json(),

  history: (window: string): Promise<SystemHistory> =>
    request.get('monitor/history', { searchParams: { window } }).json(),

  dbHistory: (window: string): Promise<DatabaseHistory> =>
    request.get('database/history', { searchParams: { window } }).json(),

  systemInfo: (): Promise<SystemInfo> => request.get('system').json(),

  waf: (window: string): Promise<WafAnalytics> =>
    request.get('waf', { searchParams: { window } }).json(),

  storage: (): Promise<StorageBreakdown> =>
    unwrap(request.get('storage', { searchParams: { refresh_if_stale: '1' } }).json()),

  refreshBench: (): Promise<TaskPayload> =>
    unwrap(request.post('storage/actions/refresh').json()),
}
