import { request, unwrap } from '@/api/client'
import type { AppInfo, MarketplaceApp } from '@/types/apps'
import type { TaskPayload } from '@/types/tasks'

export const appsApi = {
  marketplace: (): Promise<MarketplaceApp[]> => unwrap(request.get('marketplace/apps').json()),
  installed: (): Promise<AppInfo[]> => unwrap(request.get('apps').json()),
  fetchUpdates: (): Promise<TaskPayload> => request.post('apps/fetch').json(),

  add: (payload: Record<string, unknown>): Promise<TaskPayload> =>
    request.post('apps', { json: payload }).json(),
}
