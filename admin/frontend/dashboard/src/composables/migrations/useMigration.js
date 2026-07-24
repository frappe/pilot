import { computed, onUnmounted, ref } from 'vue'
import { migrationsApi, isActive, needsAttention } from '@/api/migrations'
import { useAppUpdates } from '@/composables/apps/useAppUpdates'
import { stateLabel } from '@/utils/migrationFormat'

const current = ref(null)
const loaded = ref(false)
const POLL_INTERVAL_MS = 3000
let timer = null

export function useMigration() {
  const { updatesAvailable, checked, check } = useAppUpdates()

  async function load() {
    const wasActive = isActive(current.value)
    try {
      current.value = await migrationsApi.current()
    } catch {
      current.value = null
    } finally {
      if (wasActive && !isActive(current.value)) check()
      loaded.value = true
      schedule()
    }
  }

  function schedule() {
    clearTimeout(timer)
    if (isActive(current.value)) {
      timer = setTimeout(load, POLL_INTERVAL_MS)
    }
  }

  function stop() {
    clearTimeout(timer)
  }

  function start() {
    load()
    if (!checked.value) check()
  }

  onUnmounted(stop)

  // Priority: unresolved failure > active run > update available.
  const status = computed(() => {
    const operation = current.value
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
        icon: 'lucide-loader-circle',
      }
    }
    if (updatesAvailable.value) {
      return { kind: 'update_available', label: 'Update available', icon: 'lucide-circle-arrow-up' }
    }
    return null
  })

  return { current, loaded, status, load, start }
}
