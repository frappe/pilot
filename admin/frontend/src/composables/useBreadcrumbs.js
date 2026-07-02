import { ref } from 'vue'

const items = ref(null)

export function useBreadcrumbs() {
  return {
    items,
    setBreadcrumbs: (value) => (items.value = value),
    resetBreadcrumbs: () => (items.value = null),
  }
}
