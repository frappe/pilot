import { request } from '@/api/client'
import type {
  DatabaseHistory,
  SystemHistory,
  SystemInfo,
  SystemMetrics,
  WafAnalytics,
} from '@/types/stats'
import type { StorageBreakdown } from '@/types/storage'

export const monitorApi = {
  stats: (): Promise<SystemMetrics> => request.get('metrics').json(),

  history: (window: string): Promise<SystemHistory> =>
    request.get('monitor/history', { searchParams: { window } }).json(),

  dbHistory: (window: string): Promise<DatabaseHistory> =>
    request.get('database/history', { searchParams: { window } }).json(),

  systemInfo: (): Promise<SystemInfo> => request.get('system').json(),

  waf: (window: string): Promise<WafAnalytics> =>
    request.get('waf', { searchParams: { window } }).json(),

  storage: (): Promise<StorageBreakdown> => request.get('storage').json(),
}
