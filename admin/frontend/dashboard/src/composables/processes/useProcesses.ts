import { onUnmounted, ref } from 'vue'

import { processesApi } from '@/api/processes'

const pollIntervalMs = 5000

// Module scope, like useTasks and useUpdate: the list and the pause survive
// leaving the page and coming back.
const groups = ref([])
const blocked = ref(null)
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const paused = ref(false)
let timer = null

export const useProcesses = () => {
  // Scheduled after each load settles, so a slow bench never stacks requests.
  const schedule = () => {
    clearTimeout(timer)
    if (!paused.value) timer = setTimeout(load, pollIntervalMs)
  }

  const load = async () => {
    try {
      const data = await processesApi.list()
      groups.value = data.groups || []
      blocked.value = data.blocked || null
      error.value = ''
    } catch (caught) {
      error.value = caught.message || 'Could not load processes.'
    } finally {
      loading.value = false
      schedule()
    }
  }

  // `loading` drives the skeleton, so a manual refresh needs its own flag to
  // spin the button without blanking the table underneath.
  const refresh = async () => {
    refreshing.value = true
    try {
      await load()
    } finally {
      refreshing.value = false
    }
  }

  const setPaused = (value) => {
    paused.value = value
    if (value) clearTimeout(timer)
    else load()
  }

  onUnmounted(() => clearTimeout(timer))

  return { groups, blocked, loading, refreshing, error, paused, load, refresh, setPaused }
}
