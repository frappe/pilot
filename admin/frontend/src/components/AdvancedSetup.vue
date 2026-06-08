<script setup>
import { ref } from 'vue'
import { TextInput, FormControl, FormLabel } from 'frappe-ui'
import LucideChevronRight from '~icons/lucide/chevron-right'

const form = defineModel({ type: Object, required: true })
const open = ref(false)

const generalFields = [
  { key: 'app_repo', label: 'Frappe repository' },
  { key: 'http_port', label: 'HTTP port', type: 'number' },
  { key: 'socketio_port', label: 'Socket.IO port', type: 'number' },
  { key: 'redis_port', label: 'Redis port', type: 'number' },
]

const workerFields = [
  { key: 'workers_default', label: 'Default' },
  { key: 'workers_short', label: 'Short' },
  { key: 'workers_long', label: 'Long' },
]
</script>

<template>
  <div class="rounded-lg border border-outline-gray-2">
    <button
      type="button"
      class="flex w-full items-center gap-1.5 px-3 py-2 text-sm font-medium text-ink-gray-6"
      @click="open = !open"
    >
      <LucideChevronRight class="h-4 w-4 transition-transform" :class="{ 'rotate-90': open }" />
      Advanced
    </button>

    <div v-if="open" class="flex flex-col gap-4 border-t border-outline-gray-2 p-3">
      <FormControl
        v-for="field in generalFields"
        :key="field.key"
        :label="field.label"
        :type="field.type || 'text'"
        v-model="form[field.key]"
      />

      <div class="space-y-1.5">
        <FormLabel label="Workers" />
        <div class="grid grid-cols-3 gap-2">
          <div v-for="field in workerFields" :key="field.key" class="space-y-1">
            <FormLabel :label="field.label" />
            <TextInput v-model="form[field.key]" type="number" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
