<script setup lang="ts">
import { ErrorMessage, Switch } from 'frappe-ui'
import { ref } from 'vue'

import SettingsCard from '@/components/sites/settings/SettingsCard.vue'

import { sitesApi } from '@/api/sites'
import { useSite } from '@/composables/sites/useSite'
import { errorMessage } from '@/utils/error'

interface Props {
  siteName: string
}

interface GeneralSetting {
  key: string
  label: string
  description: string
  get: (config: Record<string, unknown> | undefined) => boolean
  toValue: (value: boolean) => number
}

const props = defineProps<Props>()

const { site, reload } = useSite(props.siteName)

const GeneralSettings: GeneralSetting[] = [
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

const savingKey = ref<string | null>(null)
const error = ref('')

const getValue = (s: GeneralSetting) => s.get(site.value?.site_config)

const toggle = async (s: GeneralSetting, value: boolean) => {
  savingKey.value = s.key
  error.value = ''
  try {
    await sitesApi.configuration.update(props.siteName, { [s.key]: s.toValue(value) })
    await reload()
  } catch (e) {
    error.value = errorMessage(e, 'Failed to update.')
  } finally {
    savingKey.value = null
  }
}
</script>

<template>
  <SettingsCard title="General">
    <div
      v-for="s in GeneralSettings"
      :key="s.key"
      class="flex justify-between items-center gap-x-2.5 py-4"
    >
      <div class="flex flex-col gap-1 min-w-0">
        <p class="font-medium text-ink-gray-8">{{ s.label }}</p>
        <p class="text-ink-gray-6 text-p-sm">{{ s.description }}</p>
      </div>

      <Switch
        class="ml-4 shrink-0"
        :model-value="getValue(s)"
        :disabled="savingKey === s.key"
        @update:model-value="(v: boolean) => toggle(s, v)"
      />
    </div>
  </SettingsCard>

  <ErrorMessage v-if="error" :message="error" class="mt-4" />
</template>
