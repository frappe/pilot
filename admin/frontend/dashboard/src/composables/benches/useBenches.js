import { ref } from 'vue'
import { apiErrorMessage } from '@/api/client'
import { benchesApi } from '@/api/benches'

export function useBenches() {
  const benches = ref([])
  const loading = ref(false)
  const controlLoading = ref('')
  const error = ref('')

  async function load() {
    loading.value = true
    try {
      benches.value = await benchesApi.list()
    } catch {
    } finally {
      loading.value = false
    }
  }

  async function run(name, action) {
    error.value = ''
    try {
      const result = await action()
      if (typeof result?.json === 'function') {
        if (!result.ok) {
          error.value = apiErrorMessage(await result.json())
          return false
        }
      } else if (result?.error) {
        error.value = apiErrorMessage(result)
        return false
      }
      await load()
      return true
    } catch (e) {
      error.value = e.message
      return false
    }
  }

  async function control(name, action) {
    const operation = benchesApi[action]
    if (!operation) {
      error.value = 'Unsupported bench action.'
      return false
    }
    controlLoading.value = name
    try {
      return await run(name, () => operation(name))
    } finally {
      if (controlLoading.value === name) controlLoading.value = ''
    }
  }

  function drop(name) {
    return run(name, () => benchesApi.drop(name))
  }

  return { benches, loading, controlLoading, error, load, control, drop }
}
