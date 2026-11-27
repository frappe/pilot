<script setup lang="ts">
import { Button, ErrorMessage, FormControl, Spinner, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'

import { settingsApi } from '@/api/settings'
import type { WorkerGroupSettings } from '@/types/settings'
import { errorMessage } from '@/utils/error'

interface WorkerGroupForm {
  queues: string
  count: number | string
  timeout: number | string
}

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const groups = ref<WorkerGroupForm[]>([])
const liteMode = ref(false)
const showErrors = ref(false)

const toGroupForm = (group: WorkerGroupSettings): WorkerGroupForm => {
  return {
    queues: (group.queues || []).join(', '),
    count: group.count,
    timeout: group.timeout ?? '',
  }
}

// Mirrors WorkerConfig.collapse: a blank timeout counts as Frappe's default.
const frappeDefaultTimeout = (queue: string) => (queue === 'long' ? 1500 : 300)

const effectiveTimeout = (group: WorkerGroupSettings) => {
  return group.timeout || Math.max(0, ...(group.queues || []).map(frappeDefaultTimeout))
}

const collapse = (groups: WorkerGroupSettings[]): WorkerGroupSettings[] => {
  const hasTimeout = groups.some((group) => group.timeout)
  return [
    {
      queues: [...new Set(groups.flatMap((group) => group.queues || []))],
      count: groups.reduce((total, group) => total + group.count, 0),
      timeout: hasTimeout ? Math.max(...groups.map(effectiveTimeout)) : null,
    },
  ]
}

const addGroup = () => {
  groups.value.push({ queues: '', count: 1, timeout: '' })
}

const removeGroup = (index: number) => {
  groups.value.splice(index, 1)
}

const queueList = (value: string | number) => {
  return String(value || '')
    .split(',')
    .map((queue) => queue.trim())
    .filter(Boolean)
}

const timeoutValue = (value: number | string) => {
  return String(value ?? '').trim() === '' ? null : Number(value)
}

const isPositiveInteger = (value: number | string) => {
  return Number.isInteger(Number(value)) && Number(value) >= 1
}

const validate = (group: WorkerGroupForm) => ({
  count: isPositiveInteger(group.count) ? '' : 'Must be at least 1.',
  queues: queueList(group.queues).length ? '' : 'Needs at least one queue.',
  timeout:
    timeoutValue(group.timeout) === null || isPositiveInteger(group.timeout)
      ? ''
      : 'Must be at least 1.',
})

// Errors wait for the first save, so a group just added is not marked invalid at once.
const groupErrors = computed(() => {
  return groups.value.map((group) =>
    showErrors.value ? validate(group) : { count: '', queues: '', timeout: '' },
  )
})

const hasError = () => {
  return groups.value.some((group) => Object.values(validate(group)).some(Boolean))
}

const save = async () => {
  showErrors.value = true
  if (hasError()) return

  saving.value = true
  error.value = ''
  try {
    const payload = groups.value.map((group) => ({
      queues: queueList(group.queues),
      count: Number(group.count),
      timeout: timeoutValue(group.timeout),
    }))
    const result = await settingsApi.update({ workers: payload })
    toast.success(result.restarted ? 'Saved & restarted' : 'Saved')
  } catch (e) {
    error.value = errorMessage(e, 'Failed to save.')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    liteMode.value = Boolean(data?.lite_mode?.enabled)
    const loaded = data.workers || []
    groups.value = (liteMode.value && loaded.length ? collapse(loaded) : loaded).map(toGroupForm)
  } catch (e) {
    error.value = errorMessage(e, 'Could not load settings.')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <form v-else class="space-y-4" @submit.prevent="save">
    <p v-if="liteMode" class="text-p-sm text-ink-gray-6">
      Lite mode runs one pool: the count is job threads in the bench process, shared by every queue,
      and the longest timeout applies to every queue.
    </p>

    <div
      v-for="(group, index) in groups"
      :key="index"
      class="items-start gap-3 grid"
      :class="
        liteMode
          ? 'grid-cols-[4rem_1fr_5.5rem] sm:grid-cols-[6rem_1fr_9rem]'
          : 'grid-cols-[4rem_1fr_5.5rem_auto] sm:grid-cols-[6rem_1fr_9rem_auto]'
      "
    >
      <FormControl
        v-model="group.count"
        type="number"
        min="1"
        label="Workers"
        required
        :error="groupErrors[index].count"
      />

      <FormControl
        v-model="group.queues"
        label="Queues"
        required
        placeholder="default, short, long"
        :error="groupErrors[index].queues"
      />

      <FormControl
        v-model="group.timeout"
        type="number"
        min="1"
        label="Timeout (s)"
        placeholder="Default"
        :error="groupErrors[index].timeout"
      />

      <!-- Offset by the label above each field, so the button lines up with the inputs. -->
      <Button
        v-if="!liteMode"
        class="mt-[21px]"
        icon="lucide-x"
        variant="ghost"
        label="Remove worker group"
        tooltip="Remove worker group"
        :disabled="groups.length === 1"
        @click="removeGroup(index)"
      />
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <div class="flex justify-between items-center gap-2">
      <Button
        v-if="!liteMode"
        variant="ghost"
        icon-left="lucide-plus"
        label="Add group"
        @click="addGroup"
      />

      <Button
        class="ml-auto"
        variant="solid"
        theme="gray"
        type="submit"
        label="Save"
        :loading="saving"
      />
    </div>
  </form>
</template>
