<script setup lang="ts">
import { Button, ErrorMessage, TextInput, toast } from 'frappe-ui'
import { ref } from 'vue'

import { settingsApi } from '@/api/settings'
import type { GunicornSettings } from '@/types/settings'
import { errorMessage } from '@/utils/error'

const props = defineProps<{ settings: GunicornSettings }>()

const saving = ref(false)
const error = ref('')
const workers = ref<number | string>(props.settings.workers ?? '')
const threads = ref<number | string>(props.settings.threads)
const timeout = ref<number | string>(props.settings.timeout)

const isBlank = (value: number | string) => String(value ?? '').trim() === ''

const isPositiveInteger = (value: number | string) => {
  return !isBlank(value) && Number.isInteger(Number(value)) && Number(value) >= 1
}

const validate = () => {
  if (!isBlank(workers.value) && !isPositiveInteger(workers.value))
    return 'Workers must be at least 1, or blank for one per CPU.'
  if (!isPositiveInteger(threads.value)) return 'Threads must be at least 1.'
  if (!isPositiveInteger(timeout.value)) return 'Timeout must be at least 1 second.'
  return ''
}

const save = async () => {
  error.value = validate()
  if (error.value) return

  saving.value = true
  try {
    const result = await settingsApi.update({
      gunicorn: {
        workers: isBlank(workers.value) ? null : Number(workers.value),
        threads: Number(threads.value),
        timeout: Number(timeout.value),
      },
    })
    toast.success(result.restarted ? 'Saved & restarted' : 'Saved')
  } catch (e) {
    error.value = errorMessage(e, 'Failed to save.')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="space-y-3">
    <form
      class="dark:bg-surface-gray-1 border border-outline-gray-1 dark:border-transparent rounded-7 overflow-hidden"
      @submit.prevent="save"
    >
      <div class="items-center gap-x-3 gap-y-2 grid grid-cols-3 p-4">
        <span class="text-ink-gray-5 text-sm">Workers</span>
        <span class="text-ink-gray-5 text-sm">Threads</span>
        <span class="text-ink-gray-5 text-sm">Timeout (s)</span>

        <TextInput
          v-model="workers"
          type="number"
          min="1"
          :placeholder="`Auto (${settings.default_workers})`"
        />
        <TextInput v-model="threads" type="number" min="1" />
        <TextInput v-model="timeout" type="number" min="1" />
      </div>

      <footer class="flex justify-end px-3 py-2.5 border-outline-gray-2 border-t">
        <Button variant="solid" type="submit" label="Save" :loading="saving" />
      </footer>
    </form>

    <ErrorMessage v-if="error" :message="error" />
  </div>
</template>
