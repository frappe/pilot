import { ref } from 'vue'

import { notificationsApi } from '@/api/notifications'
import type { Notification } from '@/types/notification'

interface NotificationFilters {
  category?: string
  unreadOnly?: boolean
}

interface NotificationPage {
  data: Notification[]
  meta: { limit: number; next_cursor: string | null; unread: number }
}

const pageSize = 20

const searchParams = (filters: NotificationFilters, forCursor?: string | null) => {
  const params: Record<string, string | number> = { limit: pageSize }

  if (filters.category) params.category = filters.category
  if (filters.unreadOnly) params.unread_only = '1'
  if (forCursor) params.cursor = forCursor

  return params
}

export const useNotifications = () => {
  const notifications = ref<Notification[]>([])
  const unread = ref(0)
  const loading = ref(false)
  const loadingMore = ref(false)
  const error = ref('')
  const cursor = ref<string | null>(null)

  let shownFilters: NotificationFilters = {}
  let newestRequest = 0
  let newestLocalChange = 0

  const load = async (filters: NotificationFilters = {}) => {
    const request = ++newestRequest

    shownFilters = filters
    loading.value = true
    error.value = ''
    cursor.value = null

    try {
      const page: NotificationPage = await notificationsApi.list(searchParams(filters))

      if (request !== newestRequest) return

      notifications.value = page.data
      cursor.value = page.meta.next_cursor
      unread.value = page.meta.unread
    } catch (caught: any) {
      if (request !== newestRequest) return

      error.value = caught.message || 'Failed to load notifications'
      notifications.value = []
    } finally {
      if (request === newestRequest) loading.value = false
    }
  }

  const loadMore = async (filters: NotificationFilters = {}) => {
    if (!cursor.value || loadingMore.value) return

    const request = newestRequest

    loadingMore.value = true

    try {
      const page: NotificationPage = await notificationsApi.list(
        searchParams(filters, cursor.value),
      )

      if (request !== newestRequest) return

      notifications.value = [...notifications.value, ...page.data]
      cursor.value = page.meta.next_cursor
    } catch (caught: any) {
      if (request !== newestRequest) return

      error.value = caught.message || 'Failed to load more notifications'
    } finally {
      loadingMore.value = false
    }
  }

  const refreshBadge = async () => {
    const request = newestRequest
    const localChange = newestLocalChange
    const page: NotificationPage | null = await notificationsApi.list({ limit: 1 }).catch(() => null)

    if (!page || request !== newestRequest || localChange !== newestLocalChange) return

    unread.value = page.meta.unread
  }

  const markAsRead = async (name: string) => {
    const row = notifications.value.find((item) => item.name === name)

    if (!row || row.is_read) return

    newestLocalChange += 1
    row.is_read = true
    unread.value = Math.max(0, unread.value - 1)

    try {
      await notificationsApi.markRead(name)

      const shown = notifications.value.find((item) => item.name === name)

      if (shown) shown.is_read = true
    } catch {
      await load(shownFilters)
    }
  }

  const markAllAsRead = async () => {
    const request = newestRequest

    newestLocalChange += 1

    for (const item of notifications.value) item.is_read = true
    unread.value = 0

    try {
      await notificationsApi.markAllRead()

      if (request !== newestRequest) await load(shownFilters)
    } catch {
      await load(shownFilters)
    }
  }

  return {
    notifications,
    unread,
    loading,
    loadingMore,
    error,
    hasMore: cursor,
    load,
    loadMore,
    refreshBadge,
    markAsRead,
    markAllAsRead,
  }
}
