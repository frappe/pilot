import { ref, computed } from 'vue'

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']

export function hashColor(name) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0
  return COLORS[Math.abs(h) % COLORS.length]
}

export function useAppRegistry() {
  const registry = ref([])
  const logoMap = computed(() => Object.fromEntries(registry.value.map(a => [a.name, a.logo_url])))
  const titleMap = computed(() => Object.fromEntries(registry.value.map(a => [a.name, a.title])))

  async function loadRegistry() {
    try {
      registry.value = await fetch('/api/apps/marketplace').then(r => r.json())
    } catch { registry.value = [] }
  }

  return { registry, logoMap, titleMap, loadRegistry }
}
