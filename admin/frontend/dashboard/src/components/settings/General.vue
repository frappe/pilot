<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Switch
      label="Allow developer mode"
      description="Enables per-site developer mode and code editor."
      :model-value="allowDeveloperMode"
      :disabled="saving"
      @update:model-value="toggleAllowDeveloperMode"
    />

    <ErrorMessage v-if="error" :message="error" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ErrorMessage, Switch, toast } from 'frappe-ui'
import { settingsApi } from '@/api/settings'
import { useSession } from '@/composables/auth/useSession'

const { session } = useSession()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const allowDeveloperMode = ref(false)

async function toggleAllowDeveloperMode(value) {
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

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    allowDeveloperMode.value = Boolean(data?.bench?.allow_developer_mode)
  } catch {
    error.value = 'Could not load settings.'
  } finally {
    loading.value = false
  }
})
</script>
