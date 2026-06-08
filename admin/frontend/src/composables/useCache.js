import { ref, onMounted } from 'vue'

export function useCache(url) {
  const data = ref(null)
  const loading = ref(true)
  const error = ref('')

  async function refresh() {
    try {
      const res = await fetch(url)
      if (!res.ok) throw new Error(`${res.status}`)
      const fresh = await res.json()
      data.value = fresh
      try { sessionStorage.setItem(`bench:${url}`, JSON.stringify(fresh)) } catch {}
    } catch (e) {
      if (!data.value) error.value = e.message
    } finally {
      loading.value = false
    }
  }

  onMounted(() => {
    try {
      const cached = sessionStorage.getItem(`bench:${url}`)
      if (cached) {
        data.value = JSON.parse(cached)
        loading.value = false
      }
    } catch {}
    refresh()
  })

  return { data, loading, error, refresh }
}
