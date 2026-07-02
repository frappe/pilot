<template>
  <div>
    <p class="font-semibold text-ink-gray-8 text-base">General</p>
    <div class="mt-1 [&_[data-slot='label']]:text-sm [&_div:has([data-slot='description'])]:mt-0.5">
      <div v-for="s in GeneralSettings" :key="s.key"
        class="py-4 border-b last:border-b-0 border-outline-alpha-gray-1">
        <Switch :label="s.label" :description="s.description" :model-value="getValue(s)"
          :disabled="savingKey === s.key" @update:model-value="(v) => toggle(s, v)" />
      </div>
    </div>
    <ErrorMessage v-if="error" :message="error" class="mt-4" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ErrorMessage, Switch } from 'frappe-ui'
import { useSite } from '@/composables/useSite'
import { sitesApi } from '@/api/sites'

const props = defineProps({ siteName: { type: String, required: true } })

const { site, reload } = useSite(props.siteName)

const GeneralSettings = [
  {
    key: 'maintenance_mode',
    label: 'Maintenance mode',
    description: 'Visitors see a "back soon" page while you work.',
    get: (c) => !!(c?.maintenance_mode),
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
    get: (c) => !!(c?.developer_mode),
    toValue: (v) => (v ? 1 : 0),
  },
]

const savingKey = ref(null)
const error = ref('')

const getValue = (s) => s.get(site.value?.site_config)

async function toggle(s, value) {
  savingKey.value = s.key
  error.value = ''
  try {
    const next = { ...site.value.site_config, [s.key]: s.toValue(value) }
    await sitesApi.config(props.siteName, next)
    await reload()
  } catch (e) {
    error.value = e.message || 'Failed to update.'
  } finally {
    savingKey.value = null
  }
}
</script>
