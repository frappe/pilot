import { ref } from 'vue'
import type { RouteLocationRaw } from 'vue-router'

export interface Breadcrumb {
  label: string
  route?: RouteLocationRaw
}

const items = ref<Breadcrumb[] | null>(null)

export const useBreadcrumbs = () => {
  return {
    items,
    setBreadcrumbs: (value: Breadcrumb[]) => (items.value = value),
    resetBreadcrumbs: () => (items.value = null),
  }
}
