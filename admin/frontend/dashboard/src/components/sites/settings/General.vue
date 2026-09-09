<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ErrorMessage } from 'frappe-ui'

import SettingsSwitch from '@/components/settings/SettingsSwitch.vue'

import { useSite } from '@/composables/sites/useSite'
import { sitesApi } from '@/api/sites'
import { settingsApi } from '@/api/settings'

interface Props {
  siteName: string
}

const props = defineProps<Props>()

const { site, reload } = useSite(props.siteName)

const GeneralSettings = [
  {
    key: 'maintenance_mode',
    label: 'Maintenance mode',
    description: 'Visitors see a "back soon" page while you work.',
    get: (c) => !!c?.maintenance_mode,
    toValue: (v) => (v ? 1 : 0),
  },
  {
    key: 'pause_scheduler',
    label: 'Background jobs',
    description: 'Scheduled emails, reports and automations.',
    get: (c) => !c?.pause_scheduler,
    toValue: (v) => (v ? 0 : 1),
  },
  {
    key: 'developer_mode',
    label: 'Developer mode',
    description: 'Lets developers customise doctypes on this site.',
    get: (c) => !!c?.developer_mode,
    toValue: (v) => (v ? 1 : 0),
  },
]

const savingKey = ref(null)
const error = ref('')
const allowDeveloperMode = ref(false)

const visibleSettings = computed(() =>
  GeneralSettings.filter((s) => s.key !== 'developer_mode' || allowDeveloperMode.value),
)

onMounted(async () => {
  try {
    const settings = await settingsApi.get()
    allowDeveloperMode.value = Boolean(settings?.bench?.allow_developer_mode)
  } catch {
    allowDeveloperMode.value = false
  }
})

const getValue = (s) => s.get(site.value?.site_config)

const toggle = async (s, value) => {
  savingKey.value = s.key
  error.value = ''
  try {
    await sitesApi.configuration.update(props.siteName, { [s.key]: s.toValue(value) })
    await reload()
  } catch (e) {
    error.value = e.message || 'Failed to update.'
  } finally {
    savingKey.value = null
  }
}
</script>

<template>
  <h2 class="mb-3 text-base-semibold text-ink-gray-8">General</h2>

  <SettingsSwitch
    v-for="s in visibleSettings"
    :key="s.key"
    class="py-4 border-b last:border-b-0 border-outline-alpha-gray-1"
    :label="s.label"
    :description="s.description"
    :model-value="getValue(s)"
    :disabled="savingKey === s.key"
    @update:model-value="(v) => toggle(s, v)"
  />

  <ErrorMessage v-if="error" :message="error" class="mt-4" />
</template>
