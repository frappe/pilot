import { computed, ref } from 'vue'
import { migrationsApi, isActive, isResolved, needsAttention } from '@/api/migrations'
import { useAppUpdates } from '@/composables/apps/useAppUpdates'

const current = ref(null)
const loaded = ref(false)
const POLL_INTERVAL_MS = 3000
let timer = null

const STATE_LABELS = {
  preparing: 'Preparing',
  updating: 'Updating',
  migrating: 'Migrating',
  retrying: 'Retrying',
  restoring: 'Restoring',
}

export function useMigration() {
  const { updatesAvailable, checked, check } = useAppUpdates()

  async function load() {
    try {
      current.value = await migrationsApi.current()
    } catch {
      current.value = null
    } finally {
      loaded.value = true
      schedule()
    }
  }

  function schedule() {
    clearTimeout(timer)
    if (current.value && !isResolved(current.value)) {
      timer = setTimeout(load, POLL_INTERVAL_MS)
    }
  }

  function start() {
    load()
    if (!checked.value) check()
  }

  // Priority: unresolved failure > active run > update available.
  const status = computed(() => {
    const operation = current.value
    if (needsAttention(operation)) {
      return {
        kind: 'failed',
        label: operation.kind === 'update' ? 'Update failed' : 'Migration failed',
        operationId: operation.id,
        icon: 'lucide-circle-alert',
        variant: 'outline',
      }
    }
    if (isActive(operation)) {
      return {
        kind: 'active',
        label: STATE_LABELS[operation.state] || 'Working',
        operationId: operation.id,
        icon: 'lucide-loader-circle',
        variant: 'outline',
      }
    }
    if (updatesAvailable.value) {
      return { kind: 'update_available', label: 'Update available', icon: 'lucide-circle-arrow-up', variant: 'outline' }
    }
    return null
  })

  return { current, loaded, status, load, start }
}
