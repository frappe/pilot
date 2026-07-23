<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Switch label="Allow developer mode"
      description="Lets developer mode be turned on per site from each site's settings."
      :model-value="allowDeveloperMode" :disabled="saving"
      @update:model-value="toggleAllowDeveloperMode" />

    <ErrorMessage v-if="error" :message="error" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ErrorMessage, Switch, toast } from 'frappe-ui'
import { settingsApi } from '@/api/settings'

const loading = ref(true)
const saving = ref(false)
const error = ref(null)
const allowDeveloperMode = ref(false)

onMounted(async () => {
  try {
    const settings = await settingsApi.get()
    allowDeveloperMode.value = Boolean(settings?.bench?.allow_developer_mode)
  } catch {
    error.value = 'Could not load settings.'
  } finally {
    loading.value = false
  }
})

async function toggleAllowDeveloperMode(value) {
  saving.value = true
  error.value = null
  try {
    await settingsApi.update({ bench: { allow_developer_mode: value } })
    allowDeveloperMode.value = value
    toast.success(`Developer mode ${value ? 'allowed' : 'disallowed'}`)
  } catch (e) {
    error.value = e.message || 'Could not update developer mode setting.'
  } finally {
    saving.value = false
  }
}
</script>
