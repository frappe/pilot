import { toast } from 'frappe-ui'
import { computed, type Ref, ref } from 'vue'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import type { SiteTemplate } from '@/types/sites'
import { errorMessage } from '@/utils/error'
import { findMatchingTemplate, resolveTemplateApps, type SiteAppChoice } from '@/utils/siteApps'

/** Saved app selections for the new-site dialog, applied to its `selectedApps`. */
export const useSiteTemplates = (choices: Ref<SiteAppChoice[]>, selectedApps: Ref<string[]>) => {
  const templates = ref<SiteTemplate[]>([])
  const loaded = ref(false)
  const saving = ref(false)

  // Derived from the selection, so an adjusted selection stops showing a template.
  const active = computed(() =>
    findMatchingTemplate(templates.value, choices.value, selectedApps.value),
  )
  const missingApps = computed(() =>
    active.value ? resolveTemplateApps(active.value, choices.value).missing : [],
  )

  const titles = computed(
    () => new Map(choices.value.map((app) => [app.name, app.title || app.name])),
  )
  const options = computed(() =>
    templates.value.map((template) => {
      const { apps } = resolveTemplateApps(template, choices.value)
      return {
        label: template.name,
        value: template.name,
        description: apps.map((app) => titles.value.get(app)).join(', '),
        disabled: !apps.length,
      }
    }),
  )

  const find = (name: string) => {
    const wanted = name.trim().toLowerCase()
    return templates.value.find((template) => template.name.toLowerCase() === wanted)
  }

  const load = async () => {
    try {
      templates.value = (await sitesApi.templates.list()).templates || []
    } catch {
      templates.value = []
    }
    loaded.value = true
  }

  const apply = (name: string) => {
    const template = find(name)
    if (template) selectedApps.value = resolveTemplateApps(template, choices.value).apps
  }

  const save = async (name: string) => {
    saving.value = true
    try {
      await sitesApi.templates.save(name.trim(), selectedApps.value)
      await load()
      toast.success('Template saved')
      return true
    } catch (caught) {
      toast.error(errorMessage(caught, 'Could not save the template.'))
      return false
    } finally {
      saving.value = false
    }
  }

  const remove = async (name: string) => {
    try {
      const response = await sitesApi.templates.remove(name)
      if (!response.ok) {
        toast.error(apiErrorMessage(await response.json(), 'Could not delete the template.'))
        return
      }
      await load()
      toast.success('Template deleted')
    } catch (caught) {
      toast.error(errorMessage(caught, 'Could not delete the template.'))
    }
  }

  return {
    templates,
    options,
    loaded,
    saving,
    active,
    missingApps,
    find,
    load,
    apply,
    save,
    remove,
  }
}
