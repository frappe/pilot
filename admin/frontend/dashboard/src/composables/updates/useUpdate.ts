import { computed, onUnmounted, ref } from 'vue'

import { isActive, isPending, needsAttention, updatesApi } from '@/api/updates'
import { useAppUpdates } from '@/composables/apps/useAppUpdates'
import type { MigrationSummary } from '@/types/migrations'
import { pendingActionLabel, stateLabel } from '@/utils/updateFormat'

const current = ref<MigrationSummary | null>(null)
const loaded = ref(false)
const POLL_INTERVAL_MS = 3000
let timer: ReturnType<typeof setTimeout> | undefined

export const useUpdate = () => {
  const { updatesAvailable, checked, check } = useAppUpdates()

  const load = async () => {
    const wasActive = isActive(current.value)
    try {
      current.value = await updatesApi.current()
    } catch {
      current.value = null
    } finally {
      if (wasActive && !isActive(current.value)) check()
      loaded.value = true
      schedule()
    }
  }

  const schedule = () => {
    clearTimeout(timer)
    if (isActive(current.value) || isPending(current.value)) {
      timer = setTimeout(load, POLL_INTERVAL_MS)
    }
  }

  const stop = () => {
    clearTimeout(timer)
  }

  const start = () => {
    load()
    if (!checked.value) check()
  }

  onUnmounted(stop)

  // Priority: unresolved failure > active run > update available.
  const status = computed(() => {
    const operation = current.value
    if (operation) {
      if (isPending(operation)) {
        return {
          kind: 'active',
          label: pendingActionLabel(operation.pending_action),
          operationId: operation.id,
        }
      }

      if (needsAttention(operation)) {
        return {
          kind: 'failed',
          label: operation.kind === 'update' ? 'Update failed' : 'Migration failed',
          operationId: operation.id,
          icon: 'lucide-circle-alert',
        }
      }

      if (isActive(operation)) {
        return {
          kind: 'active',
          label: stateLabel(operation.state),
          operationId: operation.id,
        }
      }
    }

    if (updatesAvailable.value) {
      return { kind: 'update_available', label: 'Update available', icon: 'lucide-circle-arrow-up' }
    }

    return null
  })

  return { current, loaded, status, load, start }
}
