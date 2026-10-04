import { apiErrorMessage, request, unwrap } from '@/api/client'
import type { NotificationPage } from '@/types/notification'

const mutate = async (pending: Promise<Response>) => {
  const response = await pending

  if (response.ok) return

  const payload = await response.json().catch(() => null)
  throw new Error(apiErrorMessage(payload, 'Could not update the notification.'))
}

export const notificationsApi = {
  list: (params: Record<string, string | number>): Promise<NotificationPage> =>
    unwrap(request.get('notifications', { searchParams: params }).json()),

  markRead: (name: string) =>
    mutate(request.post(`notifications/${encodeURIComponent(name)}/read`)),

  markAllRead: () => mutate(request.post('notifications/read-all')),
}
