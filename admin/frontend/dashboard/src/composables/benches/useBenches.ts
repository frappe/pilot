import { ref } from 'vue'

import { benchesApi } from '@/api/benches'
import { apiErrorMessage, hasApiError } from '@/api/client'
import type { BenchResource } from '@/types/benches'
import { errorMessage } from '@/utils/error'

type BenchControlAction = 'start' | 'stop' | 'restart'

export const useBenches = () => {
  const benches = ref<BenchResource[]>([])
  const loading = ref(false)
  const controlLoading = ref('')
  const error = ref('')

  const load = async () => {
    loading.value = true
    try {
      benches.value = await benchesApi.list()
    } catch {
    } finally {
      loading.value = false
    }
  }

  const run = async (action: () => Promise<unknown>) => {
    error.value = ''
    try {
      const result = await action()
      if (result instanceof Response) {
        if (!result.ok) {
          error.value = apiErrorMessage(await result.json())
          return false
        }
      } else if (hasApiError(result)) {
        error.value = apiErrorMessage(result)
        return false
      }
      await load()
      return true
    } catch (caught) {
      error.value = errorMessage(caught, 'Bench action failed')
      return false
    }
  }

  const control = async (name: string, action: BenchControlAction) => {
    const operation = benchesApi[action]
    if (!operation) {
      error.value = 'Unsupported bench action.'
      return false
    }
    controlLoading.value = name
    try {
      return await run(() => operation(name))
    } finally {
      if (controlLoading.value === name) controlLoading.value = ''
    }
  }

  const drop = (name: string) => {
    return run(() => benchesApi.drop(name))
  }

  return { benches, loading, controlLoading, error, load, control, drop }
}
