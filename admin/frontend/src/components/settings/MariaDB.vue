<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-4">
    <div>
      <TextInput label="InnoDB buffer pool size (MB)" v-model="bufferPoolSizeMb" type="number" min="1" />
      <p class="mt-1.5 text-ink-gray-6 text-p-sm">
        Recommended {{ memory.recommended_mb }} MB · available {{ memory.max_mb }} MB
      </p>
    </div>

    <div class="flex justify-between items-center py-2.5 border-y border-outline-gray-1">
      <span class="text-ink-gray-7 text-sm">Available for MariaDB</span>
      <span class="text-ink-gray-9 text-sm">{{ memory.ram_for_mariadb_mb || 0 }} MB</span>
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <div class="flex justify-end gap-2">
      <Button variant="solid" :loading="saving" @click="save">Save Changes</Button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Alert, Button, ErrorMessage, TextInput, toast } from 'frappe-ui'
import { settingsApi } from '@/api/settings'

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const bufferPoolSizeMb = ref(0)
const memory = ref({ min_mb: 1, max_mb: 1, recommended_mb: 1 })


function validate() {
  const value = Number(bufferPoolSizeMb.value)
  if (!Number.isInteger(value)) return 'Buffer pool size must be a whole number of MB.'
  if (value < memory.value.min_mb) return `Buffer pool size must be at least ${memory.value.min_mb} MB.`
  if (value > memory.value.max_mb) return `Buffer pool size cannot exceed ${memory.value.max_mb} MB.`
  return ''
}

async function load() {
  loading.value = true
  try {
    const data = await settingsApi.get()
    memory.value = data.mariadb?.memory || memory.value
    bufferPoolSizeMb.value = memory.value.innodb_buffer_pool_size_mb || memory.value.recommended_mb
  } finally {
    loading.value = false
  }
}

async function save() {
  error.value = validate()
  if (error.value) return

  saving.value = true
  try {
    const result = await settingsApi.update({
      mariadb: { innodb_buffer_pool_size_mb: Number(bufferPoolSizeMb.value) },
    })
    if (!result.ok) {
      error.value = result.error || 'Failed to save MariaDB settings.'
      return
    }
    if (result.mariadb_restart_error) toast.error(result.mariadb_restart_error)
    else toast.success(result.mariadb_restarted ? 'Saved & restarted MariaDB' : 'Saved')
    await load()
  } catch (e) {
    error.value = e.message || 'Failed to save MariaDB settings.'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
