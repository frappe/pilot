import { ref } from 'vue'

import { auditApi } from '@/api/audit'
import type { AuditEntry } from '@/types/audit'
import type { AuditPage } from '@/types/settings'
import { errorMessage } from '@/utils/error'

export interface ActivityFilters {
  type?: string
  site?: string
  status?: string
  since?: string
  until?: string
}

// Drops unset filters so the list and the CSV export send the same query.
export const activityFilterParams = (filters: ActivityFilters): Record<string, string> =>
  Object.fromEntries(Object.entries(filters).filter(([, value]) => value)) as Record<string, string>

const auditParams = (filters: ActivityFilters, forCursor?: string | null) => ({
  ...activityFilterParams(filters),
  ...(forCursor && { cursor: forCursor }),
  limit: 50,
})

export const useActivities = () => {
  const activities = ref<AuditEntry[]>([])
  const loading = ref(false)
  const loadingMore = ref(false)
  const error = ref('')
  const cursor = ref<string | null>(null)

  const load = async (filters: ActivityFilters = {}) => {
    loading.value = true
    error.value = ''
    cursor.value = null
    try {
      const page: AuditPage = await auditApi.list(auditParams(filters))
      activities.value = page.data
      cursor.value = page.meta.next_cursor
    } catch (caught) {
      error.value = errorMessage(caught, 'Failed to load activity')
      activities.value = []
    } finally {
      loading.value = false
    }
  }

  const loadMore = async (filters: ActivityFilters = {}) => {
    if (!cursor.value || loadingMore.value) return
    loadingMore.value = true
    try {
      const page: AuditPage = await auditApi.list(auditParams(filters, cursor.value))
      activities.value = [...activities.value, ...page.data]
      cursor.value = page.meta.next_cursor
    } catch (caught) {
      error.value = errorMessage(caught, 'Failed to load more activity')
    } finally {
      loadingMore.value = false
    }
  }

  return { activities, loading, loadingMore, error, hasMore: cursor, load, loadMore }
}
