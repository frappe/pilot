<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Switch label="Developer mode"
      description="Develop custom frappe apps."
      :model-value="developerMode" :disabled="saving" @update:model-value="save" />
    <ErrorMessage v-if="error" :message="error" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ErrorMessage, Switch, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { settingsApi } from '@/api/settings'
import { useSession } from '@/composables/auth/useSession'

const { session } = useSession()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const developerMode = ref(false)

async function save(value) {
  saving.value = true
  error.value = ''
  try {
    const result = await settingsApi.update({ admin: { developer_mode: value } })
    if (result.error) {
      error.value = apiErrorMessage(result, 'Failed to save.')
      return
    }
    developerMode.value = value
    session.developerMode = value
    toast.success(value ? 'Developer mode enabled' : 'Developer mode disabled')
  } catch (e) {
    error.value = e.message || 'Failed to save.'
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    developerMode.value = !!data.admin?.developer_mode
  } finally {
    loading.value = false
  }
})
</script>
