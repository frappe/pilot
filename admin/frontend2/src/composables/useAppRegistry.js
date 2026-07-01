import { ref, computed } from 'vue'
import { appsApi } from '@/api/apps'

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']

const registry = ref([])
const loaded = ref(false)

export function hashColor(name) {
  let hash = 0
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) | 0
  return COLORS[Math.abs(hash) % COLORS.length]
}

export function useAppRegistry() {
  async function load() {
    if (loaded.value) return
    try {
      registry.value = await appsApi.marketplace()
      loaded.value = true
    } catch {
      registry.value = []
    }
  }

  const logoMap = computed(() =>
    Object.fromEntries(
      registry.value
        .filter((app) => app.logo_url)
        .map((app) => [app.name, app.logo_url]),
    ),
  )

  const titleMap = computed(() =>
    Object.fromEntries(
      registry.value.map((app) => [app.name, app.title || app.name]),
    ),
  )

  const descriptionMap = computed(() =>
    Object.fromEntries(
      registry.value.map((app) => [app.name, app.description || '']),
    ),
  )

  const documentationMap = computed(() =>
    Object.fromEntries(
      registry.value
        .filter((app) => app.documentation)
        .map((app) => [app.name, app.documentation]),
    ),
  )

  const websiteMap = computed(() =>
    Object.fromEntries(
      registry.value
        .filter((app) => app.website)
        .map((app) => [app.name, app.website]),
    ),
  )

  return {
    registry,
    loaded,
    load,
    logoMap,
    titleMap,
    descriptionMap,
    documentationMap,
    websiteMap,
    hashColor,
  }
}
