<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-4">
    <div v-for="(group, index) in groups" :key="index" class="flex items-end gap-3">
      <div class="space-y-1.5 w-28">
        <p v-if="index === 0" class="font-medium text-ink-gray-7 text-sm">No of Workers</p>
        <TextInput v-model="group.count" type="number" min="1" class="w-full" />
      </div>
      <div class="flex-1 space-y-1.5">
        <p v-if="index === 0" class="font-medium text-ink-gray-7 text-sm">Queues</p>
        <TextInput v-model="group.queues" placeholder="default, short, long" class="w-full" />
      </div>
      <Button variant="subtle" icon="lucide-x" :disabled="groups.length === 1" @click="removeGroup(index)" />
    </div>


    <ErrorMessage v-if="error" :message="error" />

    <div class="flex justify-end gap-2">
      <Button variant="solid" :loading="saving" @click="save">Save Changes</Button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Button, ErrorMessage, TextInput, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { settingsApi } from '@/api/settings'

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const groups = ref([])

function toGroupForm(group) {
  return { queues: (group.queues || []).join(', '), count: group.count }
}

function addGroup() {
  groups.value.push({ queues: '', count: 1 })
}

function removeGroup(index) {
  groups.value.splice(index, 1)
}

defineExpose({ addGroup })

function queueList(value) {
  return String(value || '')
    .split(',')
    .map((queue) => queue.trim())
    .filter(Boolean)
}

function validate() {
  for (const [index, group] of groups.value.entries()) {
    if (!queueList(group.queues).length) return `Worker group ${index + 1} needs at least one queue.`
    const count = Number(group.count)
    if (!Number.isInteger(count) || count < 1) return `Worker group ${index + 1} count must be at least 1.`
  }
  return ''
}

async function save() {
  error.value = validate()
  if (error.value) return

  saving.value = true
  try {
    const payload = groups.value.map((group) => ({ queues: queueList(group.queues), count: Number(group.count) }))
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
    groups.value = (data.workers || []).map(toGroupForm)
  } finally {
    loading.value = false
  }
})
</script>
