import { computed, ref } from 'vue'
import type { AvatarTheme } from 'frappe-ui'

import { appsApi } from '@/api/apps'
import type { MarketplaceApp } from '@/types/apps'
import { errorMessage } from '@/utils/error'

const THEMES: AvatarTheme[] = ['violet', 'blue', 'green', 'amber', 'red']
export const FRAPPE_LOGO_URL =
  'https://raw.githubusercontent.com/frappe/frappe/refs/heads/develop/.github/framework-logo-new.svg'

const registry = ref<MarketplaceApp[]>([])
const loaded = ref(false)
const error = ref('')
// Pages mount several registry users at once; they share one request.
let pending: Promise<void> | null = null

const fetchRegistry = async () => {
  try {
    registry.value = await appsApi.marketplace()
    loaded.value = true
    error.value = ''
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not load the app registry.')
  }
}

export const isFrappeFramework = (name: string | null | undefined) => {
  const lower = name?.toLowerCase()
  return lower === 'frappe' || lower === 'frappe framework'
}

export const hashTheme = (name: string | null | undefined) => {
  let hash = 0
  for (const char of name ?? '') hash = (hash * 31 + char.charCodeAt(0)) | 0
  return THEMES[Math.abs(hash) % THEMES.length]
}

export const useAppRegistry = () => {
  const load = () => {
    if (loaded.value) return Promise.resolve()
    pending ??= fetchRegistry().finally(() => {
      pending = null
    })
    return pending
  }

  const logoMap = computed<Record<string, string>>(() => ({
    ...Object.fromEntries(
      registry.value.filter((app) => app.logo_url).map((app) => [app.name, app.logo_url]),
    ),
    frappe: FRAPPE_LOGO_URL,
  }))

  const titleMap = computed<Record<string, string>>(() =>
    Object.fromEntries(registry.value.map((app) => [app.name, app.title || app.name])),
  )

  const descriptionMap = computed<Record<string, string>>(() =>
    Object.fromEntries(registry.value.map((app) => [app.name, app.description || ''])),
  )

  const documentationMap = computed<Record<string, string>>(() =>
    Object.fromEntries(
      registry.value.filter((app) => app.documentation).map((app) => [app.name, app.documentation]),
    ),
  )

  const websiteMap = computed<Record<string, string>>(() =>
    Object.fromEntries(
      registry.value.filter((app) => app.website).map((app) => [app.name, app.website]),
    ),
  )

  return {
    registry,
    loaded,
    error,
    load,
    logoMap,
    titleMap,
    descriptionMap,
    documentationMap,
    websiteMap,
  }
}
