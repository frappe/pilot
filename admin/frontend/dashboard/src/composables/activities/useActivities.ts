import { ref } from 'vue'

import { auditApi } from '@/api/audit'
import type { AuditEntry } from '@/types/audit'

interface ActivityFilters {
  type?: string
  site?: string
  status?: string
}

interface AuditPage {
  data: AuditEntry[]
  meta: { limit: number; next_cursor: string | null }
}

const auditParams = (filters: ActivityFilters, forCursor?: string | null) => {
  const params: Record<string, string> = {}
  if (filters.type) params.type = filters.type
  if (filters.site) params.site = filters.site
  if (filters.status) params.status = filters.status
  if (forCursor) params.cursor = forCursor
  return { ...params, limit: 50 }
}

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
    } catch (caught: any) {
      error.value = caught.message || 'Failed to load activity'
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
    } catch (caught: any) {
      error.value = caught.message || 'Failed to load more activity'
    } finally {
      loadingMore.value = false
    }
  }

  return { activities, loading, loadingMore, error, hasMore: cursor, load, loadMore }
}
