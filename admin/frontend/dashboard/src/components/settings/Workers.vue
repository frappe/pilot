<script setup lang="ts">
import { Button, ErrorMessage, Spinner, TextInput, toast } from 'frappe-ui'
import { onMounted, ref } from 'vue'

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

const validate = () => {
  for (const [index, group] of groups.value.entries()) {
    if (!queueList(group.queues).length)
      return `Worker group ${index + 1} needs at least one queue.`
    const count = Number(group.count)
    if (!Number.isInteger(count) || count < 1)
      return `Worker group ${index + 1} count must be at least 1.`
    const timeout = timeoutValue(group.timeout)
    if (timeout !== null && (!Number.isInteger(timeout) || timeout < 1))
      return `Worker group ${index + 1} timeout must be at least 1 second.`
  }
  return ''
}

const save = async () => {
  error.value = validate()
  if (error.value) return

  saving.value = true
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

  <div v-else class="space-y-3">
    <p v-if="liteMode" class="text-p-sm text-ink-gray-6">
      Lite mode runs one pool: the count is job threads in the bench process, shared by every queue,
      and the longest timeout applies to every queue.
    </p>

    <form class="bg-surface-gray-1 rounded-7 overflow-hidden" @submit.prevent="save">
      <div
        class="items-center gap-x-3 gap-y-2 grid grid-cols-[4rem_1fr_5.5rem_2rem] sm:grid-cols-[6rem_1fr_9rem_2rem] p-4"
      >
        <span class="text-ink-gray-5 text-sm">Workers</span>
        <span class="text-ink-gray-5 text-sm">Queues</span>
        <span class="text-ink-gray-5 text-sm">Timeout (s)</span>
        <span />

        <template v-for="(group, index) in groups" :key="index">
          <TextInput v-model="group.count" type="number" min="1" />
          <TextInput v-model="group.queues" placeholder="default, short, long" />
          <TextInput v-model="group.timeout" type="number" min="1" placeholder="Default" />

          <Button
            v-if="!liteMode"
            icon="lucide-x"
            variant="ghost"
            label="Remove worker group"
            tooltip="Remove worker group"
            :disabled="groups.length === 1"
            @click="removeGroup(index)"
          />
          <span v-else />
        </template>
      </div>

      <footer
        class="flex justify-between items-center gap-2 px-3 py-2.5 border-outline-gray-2 border-t"
      >
        <Button
          v-if="!liteMode"
          variant="ghost"
          icon-left="lucide-plus"
          label="Add group"
          @click="addGroup"
        />

        <Button class="ml-auto" variant="solid" type="submit" label="Save" :loading="saving" />
      </footer>
    </form>

    <ErrorMessage v-if="error" :message="error" />
  </div>
</template>
