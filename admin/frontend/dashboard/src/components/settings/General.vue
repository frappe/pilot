<script setup lang="ts">
import { ErrorMessage, Spinner, Switch, toast } from 'frappe-ui'
import { onMounted, ref } from 'vue'
import { settingsApi } from '@/api/settings'
import SettingsRow from '@/components/settings/SettingsRow.vue'
import Workers from '@/components/settings/Workers.vue'
import { useSession } from '@/composables/auth/useSession'
import { errorMessage } from '@/utils/error'

const { session } = useSession()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const allowDeveloperMode = ref(false)
const liteMode = ref(false)
const liteModeSupported = ref(false)

const toggleAllowDeveloperMode = async (value: boolean) => {
  saving.value = true
  error.value = ''
  try {
    await settingsApi.update({ bench: { allow_developer_mode: value } })
    allowDeveloperMode.value = value
    session.developerMode = value
    toast.success(`Developer mode ${value ? 'allowed' : 'disallowed'}`)
  } catch (e) {
    error.value = errorMessage(e, 'Could not update developer mode setting.')
  } finally {
    saving.value = false
  }
}

const toggleLiteMode = async (value: boolean) => {
  saving.value = true
  error.value = ''
  try {
    await settingsApi.update({ lite_mode: { enabled: value } })
    liteMode.value = value
    toast.success(`Lite mode ${value ? 'enabled' : 'disabled'}. Rebuilding the process set.`)
  } catch (e) {
    error.value = errorMessage(e, 'Could not update lite mode setting.')
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

  <template v-else>
    <ErrorMessage v-if="error" :message="error" class="mb-4" />

    <div class="-mx-3 -mt-3.5 divide-y divide-outline-alpha-gray-1 hover-merges-dividers">
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

      <section class="px-3 py-3.5">
        <h4 class="text-base-medium text-ink-gray-8">Background workers</h4>

        <p class="mt-1 mb-3 text-ink-gray-6 text-p-sm">
          Configure background worker groups and queues.
        </p>

        <Workers />
      </section>
    </div>
  </template>
</template>
