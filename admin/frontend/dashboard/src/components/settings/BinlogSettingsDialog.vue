<script setup lang="ts">
import { Button, Dialog, ErrorMessage, Spinner, TextInput } from 'frappe-ui'
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { databaseApi } from '@/api/database'
import type { DatabaseVariable } from '@/types/database'
import { errorMessage } from '@/utils/error'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface BinlogField {
  name: string
  label: string
  description: string
  unit: string
  scale: number
  variable: DatabaseVariable | null
  draft: number | string
}

const visible = defineModel<boolean>({ required: true })

const router = useRouter()

const loading = ref(false)
const saving = ref('')
const error = ref('')
const fields = ref<BinlogField[]>([])

const fieldSpecs = [
  {
    name: 'binlog_expire_logs_seconds',
    label: 'Retention',
    description: 'Purge logs older than this. 0 keeps them.',
    unit: 'days',
    scale: 86400,
  },
  {
    name: 'max_binlog_total_size',
    label: 'Size limit',
    description: 'Purge the oldest logs past this size. 0 means no limit.',
    unit: 'GB',
    scale: 1024 ** 3,
  },
]

const load = async () => {
  loading.value = true
  error.value = ''

  try {
    const snapshot = await databaseApi.configurations.list()

    fields.value = fieldSpecs.map((spec) => {
      const variable = snapshot.variables.find((item) => item.name === spec.name) ?? null
      const value = Number(variable?.value) || 0

      return { ...spec, variable, draft: Math.round(value / spec.scale) }
    })
  } catch (e) {
    error.value = errorMessage(e, 'Could not load binary log settings.')
  } finally {
    loading.value = false
  }
}

const fieldValue = (field: BinlogField) => Number(field.draft) * field.scale

const fieldError = (field: BinlogField) => {
  const draft = Number(field.draft)
  const max = field.variable?.max

  if (field.draft === '' || !Number.isInteger(draft) || draft < 0) return 'Enter a whole number.'
  if (typeof max === 'number' && fieldValue(field) > max) {
    return `Up to ${Math.floor(max / field.scale)} ${field.unit}.`
  }

  return ''
}

const isUnchanged = (field: BinlogField) => fieldValue(field) === Number(field.variable?.value)

const fieldNote = (field: BinlogField) => {
  if (!field.variable?.supported) return 'Needs MariaDB 11.4 or later.'
  if (!field.variable.editable) return field.variable.reason || 'Read-only in Pilot.'

  return fieldError(field) || field.description
}

const update = async (field: BinlogField) => {
  if (fieldError(field) || isUnchanged(field)) return

  saving.value = field.name
  error.value = ''

  const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)

  try {
    const task = await databaseApi.configurations.set(
      field.name,
      fieldValue(field),
      `database-config-${field.name}-${Date.now()}-${random}`,
    )

    visible.value = false
    openTaskDetailPage(router, task.task_id)
  } catch (e) {
    error.value = errorMessage(e, 'Could not update the binary log setting.')
  } finally {
    saving.value = ''
  }
}

const viewLogs = () => {
  visible.value = false
  router.push('/database/analyzer')
}

watch(visible, (value) => {
  if (value) load()
})
</script>

<template>
  <Dialog v-model="visible" title="Binary logs" size="md">
    <div v-if="loading" class="flex justify-center items-center h-28">
      <Spinner class="size-5 text-ink-gray-4" />
    </div>

    <div v-else class="divide-y divide-outline-alpha-gray-1">
      <div
        v-for="field in fields"
        :key="field.name"
        class="flex justify-between items-center gap-4 py-3"
      >
        <div class="flex flex-col gap-1 min-w-0">
          <span class="text-base text-ink-gray-8">{{ field.label }}</span>

          <span
            class="text-p-sm"
            :class="fieldError(field) && field.variable?.editable ? 'text-ink-red-5' : 'text-ink-gray-6'"
          >
            {{ fieldNote(field) }}
          </span>
        </div>

        <div class="flex items-center gap-2 shrink-0">
          <TextInput
            v-model="field.draft"
            type="number"
            min="0"
            class="w-28 [&_input]:!pe-12"
            :disabled="!field.variable?.editable || Boolean(saving)"
            :aria-label="`${field.label} in ${field.unit}`"
            @keydown.enter="update(field)"
          >
            <template #suffix>
              <span class="text-ink-gray-5 text-xs">{{ field.unit }}</span>
            </template>
          </TextInput>

          <Button
            label="Update"
            :loading="saving === field.name"
            :disabled="
              !field.variable?.editable ||
              Boolean(fieldError(field)) ||
              isUnchanged(field) ||
              Boolean(saving)
            "
            @click="update(field)"
          />
        </div>
      </div>
    </div>

    <ErrorMessage v-if="error" :message="error" class="mt-3" />

    <template #actions>
      <div class="flex justify-between items-center gap-2">
        <Button
          variant="ghost"
          icon-right="lucide-arrow-right"
          label="View and purge logs"
          @click="viewLogs"
        />
        <Button variant="ghost" label="Close" @click="visible = false" />
      </div>
    </template>
  </Dialog>
</template>
