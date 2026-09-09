<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ErrorMessage, Spinner, Switch, toast } from 'frappe-ui'

import SettingsRow from '@/components/settings/SettingsRow.vue'
import Version from '@/components/settings/Version.vue'
import Git from '@/components/settings/Git.vue'
import S3Bucket from '@/components/settings/S3Bucket.vue'
import LLM from '@/components/settings/LLM.vue'
import Notifications from '@/components/settings/Notifications.vue'
import Mail from '@/components/settings/Mail.vue'
import Workers from '@/components/settings/Workers.vue'

import { settingsApi } from '@/api/settings'
import { useSession } from '@/composables/auth/useSession'
import { GENERAL_SECTIONS as sections } from '@/components/settings/sections'

const openSection = defineModel<{ id: string } | null>('openSection')

const { session } = useSession()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const allowDeveloperMode = ref(false)
const liteMode = ref(false)
const liteModeSupported = ref(false)

const toggleAllowDeveloperMode = async (value) => {
  saving.value = true
  error.value = ''
  try {
    await settingsApi.update({ bench: { allow_developer_mode: value } })
    allowDeveloperMode.value = value
    session.developerMode = value
    toast.success(`Developer mode ${value ? 'allowed' : 'disallowed'}`)
  } catch (e) {
    error.value = e.message || 'Could not update developer mode setting.'
  } finally {
    saving.value = false
  }
}

const toggleLiteMode = async (value) => {
  saving.value = true
  error.value = ''
  try {
    await settingsApi.update({ lite_mode: { enabled: value } })
    liteMode.value = value
    toast.success(`Lite mode ${value ? 'enabled' : 'disabled'}. Rebuilding the process set.`)
  } catch (e) {
    error.value = e.message || 'Could not update lite mode setting.'
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    allowDeveloperMode.value = Boolean(data?.bench?.allow_developer_mode)
    liteMode.value = Boolean(data?.lite_mode?.enabled)
    liteModeSupported.value = Boolean(data?.lite_mode?.supported)
  } catch {
    error.value = 'Could not load settings.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <Git v-else-if="openSection?.id === 'github'" />
  <S3Bucket v-else-if="openSection?.id === 's3-bucket'" />
  <LLM v-else-if="openSection?.id === 'llm'" />
  <Notifications v-else-if="openSection?.id === 'notifications'" />
  <Mail v-else-if="openSection?.id === 'mail'" />
  <Workers v-else-if="openSection?.id === 'workers'" />

  <template v-else>
    <ErrorMessage v-if="error" :message="error" class="mb-4" />

    <div class="-mx-2.5 divide-y divide-outline-alpha-gray-1 hover-merges-dividers">
      <SettingsRow
        label="Allow developer mode"
        description="Enables per-site developer mode and code editor."
        interactive
        @click="!saving && toggleAllowDeveloperMode(!allowDeveloperMode)"
      >
        <Switch
          :model-value="allowDeveloperMode"
          :disabled="saving"
          @click.stop
          @update:model-value="toggleAllowDeveloperMode"
        />
      </SettingsRow>

      <SettingsRow
        v-if="liteModeSupported"
        label="Lite mode"
        description="One process for web, realtime and jobs. Less memory, best for small sites."
        interactive
        @click="!saving && toggleLiteMode(!liteMode)"
      >
        <Switch
          :model-value="liteMode"
          :disabled="saving"
          @click.stop
          @update:model-value="toggleLiteMode"
        />
      </SettingsRow>

      <SettingsRow
        v-for="section in sections"
        :key="section.id"
        as="button"
        interactive
        :label="section.label"
        :description="section.description"
        @click="openSection = section"
      >
        <span class="size-4 text-ink-gray-5 lucide-chevron-right" aria-hidden="true" />
      </SettingsRow>

      <Version />
    </div>
  </template>
</template>
