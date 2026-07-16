import { ref, computed } from 'vue'
import { sitesApi } from '@/api/sites'

const sites = ref([])
const loading = ref(false)
const error = ref('')

export function useSites() {
  async function load() {
    loading.value = true
    error.value = ''
    try {
      sites.value = await sitesApi.list()
    } catch (caught) {
      error.value = caught.message || 'Failed to load sites'
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
