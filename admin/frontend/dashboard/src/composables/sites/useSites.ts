import { computed, ref } from 'vue'

import { sitesApi } from '@/api/sites'
import type { SiteResource } from '@/types/sites'
import { errorMessage } from '@/utils/error'

const sites = ref<SiteResource[]>([])
const loading = ref(false)
const error = ref('')

export const useSites = () => {
  const load = async () => {
    loading.value = true
    error.value = ''
    try {
      sites.value = await sitesApi.list()
    } catch (caught) {
      error.value = errorMessage(caught, 'Failed to load sites')
      sites.value = []
    } finally {
      loading.value = false
    }
  }

  const names = computed(() => sites.value.map((site) => site.name))

  return {
    sites,
    loading,
    error,
    load,
    names,
  }
}
