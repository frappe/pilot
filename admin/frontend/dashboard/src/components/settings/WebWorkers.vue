<script setup lang="ts">
import { Button, ErrorMessage, FormControl, toast } from 'frappe-ui'
import { computed, ref } from 'vue'

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

const workersError = computed(() => {
  return isBlank(workers.value) || isPositiveInteger(workers.value) ? '' : 'Must be at least 1.'
})
const threadsError = computed(() => (isPositiveInteger(threads.value) ? '' : 'Must be at least 1.'))
const timeoutError = computed(() => (isPositiveInteger(timeout.value) ? '' : 'Must be at least 1.'))
const hasError = computed(() => Boolean(workersError.value || threadsError.value || timeoutError.value))

const save = async () => {
  if (hasError.value) return

  saving.value = true
  error.value = ''
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
  <form class="space-y-4" @submit.prevent="save">
    <div class="items-start gap-4 grid sm:grid-cols-3">
      <FormControl
        v-model="workers"
        type="number"
        min="1"
        label="Workers"
        :placeholder="`Auto (${settings.default_workers})`"
        :error="workersError"
      />

      <FormControl
        v-model="threads"
        type="number"
        min="1"
        label="Threads per worker"
        required
        :error="threadsError"
      />

      <FormControl
        v-model="timeout"
        type="number"
        min="1"
        label="Timeout (seconds)"
        required
        :error="timeoutError"
      />
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <div class="flex justify-end">
      <Button
        variant="solid"
        theme="gray"
        type="submit"
        label="Save"
        :loading="saving"
        :disabled="hasError"
      />
    </div>
  </form>
</template>
