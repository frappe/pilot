import { ref } from 'vue'

import { sitesApi } from '@/api/sites'
import { formatBytes } from '@/utils/format'

// The report is refreshed by the site-storage timer every six hours, so pages
// share one fetch rather than each asking for the same unchanged numbers.
const REFRESH_AFTER_MS = 60_000

const report = ref(null)
let fetchedAt = 0
let pending = null

export const useSiteStorage = () => {
  // `force` skips the shared client cache, not the server's report - the
  // numbers themselves only move when the timer runs.
  const load = (force = false) => {
    if (!force) {
      if (pending) return pending
      if (report.value && Date.now() - fetchedAt < REFRESH_AFTER_MS) return Promise.resolve()
    }
    const request = sitesApi
      .storage()
      .then((data) => {
        report.value = data
        fetchedAt = Date.now()
      })
      .catch(() => {}) // a size label; every caller renders fine without it
      .finally(() => {
        // A stale in-flight request may finish after a forced one replaced
        // it; only clear `pending` if it still points at this request.
        if (pending === request) pending = null
      })
    pending = request
    return pending
  }

  const storageLabel = (siteName) => {
    const usage = (report.value?.sites || []).find((site) => site.name === siteName)
    return usage?.total_bytes ? formatBytes(usage.total_bytes) : ''
  }

  return { load, storageLabel }
}
