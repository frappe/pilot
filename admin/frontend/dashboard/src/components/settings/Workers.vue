<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Button, ErrorMessage, Spinner, TextInput, toast } from 'frappe-ui'

import { apiErrorMessage } from '@/api/client'
import { settingsApi } from '@/api/settings'

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const groups = ref([])
const liteMode = ref(false)

const toGroupForm = (group) => {
  return { queues: (group.queues || []).join(', '), count: group.count }
}

const collapse = (groups) => {
  return [
    {
      queues: [...new Set(groups.flatMap((group) => group.queues || []))],
      count: groups.reduce((total, group) => total + group.count, 0),
    },
  ]
}

const addGroup = () => {
  groups.value.push({ queues: '', count: 1 })
}

const removeGroup = (index) => {
  groups.value.splice(index, 1)
}

const queueList = (value) => {
  return String(value || '')
    .split(',')
    .map((queue) => queue.trim())
    .filter(Boolean)
}

const validate = () => {
  for (const [index, group] of groups.value.entries()) {
    if (!queueList(group.queues).length)
      return `Worker group ${index + 1} needs at least one queue.`
    const count = Number(group.count)
    if (!Number.isInteger(count) || count < 1)
      return `Worker group ${index + 1} count must be at least 1.`
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
    }))
    const result = await settingsApi.update({ workers: payload })
    if (result.error) {
      error.value = apiErrorMessage(result, 'Failed to save.')
      return
    }
    toast.success(result.restarted ? 'Saved & restarted' : 'Saved')
  } catch (e) {
    error.value = e.message || 'Failed to save.'
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
    error.value = e.message || 'Could not load settings.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-6">
    <p v-if="liteMode" class="text-p-sm text-ink-gray-6">
      Lite mode runs one pool: the count is job threads in the bench process, shared by every
      queue.
    </p>

    <div v-for="(group, index) in groups" :key="index" class="flex items-end gap-3">
      <div class="space-y-1.5 w-28">
        <p v-if="index === 0" class="font-medium text-ink-gray-7">No of Workers</p>
        <TextInput v-model="group.count" type="number" min="1" class="w-full" />
      </div>

      <div class="flex-1 space-y-1.5">
        <p v-if="index === 0" class="font-medium text-ink-gray-7">Queues</p>
        <TextInput v-model="group.queues" placeholder="default, short, long" class="w-full" />
      </div>

      <Button
        v-if="!liteMode"
        icon="lucide-x"
        label="Remove worker group"
        tooltip="Remove worker group"
        :disabled="groups.length === 1"
        @click="removeGroup(index)"
      />
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <div class="flex justify-end gap-2">
      <Button v-if="!liteMode" icon-left="lucide-plus" @click="addGroup">Add</Button>
      <Button variant="solid" :loading="saving" @click="save">Save Changes</Button>
    </div>
  </div>
</template>
