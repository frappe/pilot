import { computed, ref, watch } from 'vue'

import { appsApi } from '@/api/apps'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import type { AppInfo } from '@/types/apps'
import { errorMessage } from '@/utils/error'
import { buildSiteAppChoices } from '@/utils/siteApps'

const validate = (name: string) => {
  if (!name) return 'Site name is required.'
  if (!/^[a-zA-Z0-9][a-zA-Z0-9\-.]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$/.test(name))
    return 'Site name must be a valid hostname.'
  return null
}

export const useNewSite = () => {
  const { registry, load: loadRegistry } = useAppRegistry()
  const benchApps = ref<AppInfo[]>([])

  const name = ref('')
  const prefix = ref('')
  const wildcardDomains = ref<string[]>([])
  const suffix = ref('')
  const selectedApps = ref<string[]>([])
  const appSearch = ref('')
  const loading = ref(false)
  const creating = ref(false)
  const error = ref('')

  const hasSingleDomain = computed(() => wildcardDomains.value.length === 1)
  const nameError = computed(() => validate(name.value.trim()))
  const availableApps = computed(() => buildSiteAppChoices(registry.value, benchApps.value))
  const frappe = computed(() => benchApps.value.find((app) => app.name === 'frappe'))
  const filteredApps = computed(() => {
    const query = appSearch.value.trim().toLowerCase()

    if (!query) return availableApps.value

    return availableApps.value.filter((app) =>
      [app.title, app.name].some((text) => text?.toLowerCase().includes(query)),
    )
  })

  watch([prefix, suffix], () => {
    name.value =
      wildcardDomains.value.length && prefix.value ? `${prefix.value.trim()}${suffix.value}` : ''
  })

  const loadBenchApps = async () => {
    try {
      benchApps.value = await appsApi.installed()
    } catch {
      benchApps.value = []
    }
  }

  const loadWildcardDomains = async () => {
    try {
      const { domains } = await sitesApi.domains.wildcardList()

      wildcardDomains.value = domains || []
      suffix.value = wildcardDomains.value[0] || ''
    } catch {
      wildcardDomains.value = []
    }
  }

  const reset = async () => {
    name.value = ''
    prefix.value = ''
    error.value = ''
    selectedApps.value = []
    appSearch.value = ''
    loading.value = true

    await Promise.all([loadWildcardDomains(), loadRegistry(), loadBenchApps()])

    loading.value = false
  }

  const toggleApp = (app: string) => {
    const index = selectedApps.value.indexOf(app)

    if (index === -1) selectedApps.value.push(app)
    else selectedApps.value.splice(index, 1)
  }

  const create = async () => {
    const siteName = name.value.trim()
    const validationError = validate(siteName)

    if (validationError) {
      error.value = validationError

      return null
    }

    creating.value = true
    error.value = ''

    try {
      const result = await sitesApi.create({ name: siteName, apps: selectedApps.value })

      if (result.task_id) return result.task_id

      error.value = apiErrorMessage(result, 'Could not create site.')
    } catch (caught) {
      error.value = errorMessage(caught, 'Could not create site.')
    } finally {
      creating.value = false
    }

    return null
  }

  return {
    name,
    prefix,
    wildcardDomains,
    suffix,
    selectedApps,
    appSearch,
    loading,
    creating,
    error,
    hasSingleDomain,
    nameError,
    availableApps,
    filteredApps,
    frappe,
    reset,
    toggleApp,
    create,
  }
}
