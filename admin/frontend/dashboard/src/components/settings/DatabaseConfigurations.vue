<script setup lang="ts">
import { Button, Dialog, ErrorMessage, Spinner, Switch, TextInput, Tooltip } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { databaseApi } from '@/api/database'
import { openTaskDetailPage } from '@/utils/taskRoute'

const router = useRouter()

const loading = ref(false)
const snapshot = ref(null)
const error = ref('')
const search = ref('')
const editing = ref(null)
const draftValue = ref(null)
const saving = ref(false)
const saveError = ref('')

const groups = computed(() => {
  const query = search.value.trim().toLowerCase()
  const grouped = new Map()
  for (const variable of snapshot.value?.variables || []) {
    const searchable = `${variable.name} ${variable.section}`.toLowerCase()
    if (query && !searchable.includes(query)) continue
    if (!grouped.has(variable.section)) grouped.set(variable.section, [])
    grouped.get(variable.section).push(variable)
  }
  return Array.from(grouped, ([name, variables]) => ({ name, variables }))
})

const editorOpen = computed({
  get: () => Boolean(editing.value),
  set: (value) => {
    if (!value && !saving.value) editing.value = null
  },
})
const editor = computed(() => editing.value?.edit || null)
const inputLabel = computed(() => {
  if (!editor.value) return 'Value'
  return editor.value.unit ? `Value (${editor.value.unit})` : 'Value'
})
const validationError = computed(() => {
  if (!editor.value) return ''
  if (editor.value.value_type === 'boolean') {
    return typeof draftValue.value === 'boolean' ? '' : 'Choose enabled or disabled.'
  }
  if (!Number.isInteger(draftValue.value)) return 'Enter a whole number.'
  if (Number.isInteger(editor.value.min) && draftValue.value < editor.value.min) {
    return `Enter a value between ${editor.value.min} and ${editor.value.max}.`
  }
  if (Number.isInteger(editor.value.max) && draftValue.value > editor.value.max) {
    return `Enter a value between ${editor.value.min} and ${editor.value.max}.`
  }
  return ''
})
const unchanged = computed(() => editor.value !== null && draftValue.value === editor.value.value)
const restartWarning = computed(() => {
  if (!editor.value) return ''
  if (editor.value.action === 'performance_schema') {
    return 'MariaDB will restart to apply this change.'
  }
  if (
    editor.value.action === 'innodb_buffer_pool_size' &&
    Number.isInteger(editor.value.dynamic_max) &&
    draftValue.value > editor.value.dynamic_max
  ) {
    return `MariaDB will restart because this value is above its current live Buffer Pool ceiling of ${formatConstraint(editor.value.dynamic_max, editor.value.unit)}.`
  }
  return editor.value.requires_restart ? 'MariaDB will restart to apply this change.' : ''
})

const formatValue = (variable) => {
  if (!variable.supported || variable.value === null) return 'Unavailable'
  if (variable.value_type === 'boolean') return variable.value ? 'Enabled' : 'Disabled'
  if (variable.unit === 'bytes' && Number.isFinite(variable.value)) {
    return formatBytes(variable.value)
  }
  return formatConstraint(variable.value, variable.unit)
}

const formatBytes = (value) => {
  const units = ['bytes', 'KB', 'MB', 'GB', 'TB']
  let amount = value
  let index = 0
  while (Math.abs(amount) >= 1024 && index < units.length - 1) {
    amount /= 1024
    index += 1
  }
  const rounded = Number.isInteger(amount) ? amount : amount.toFixed(1)
  return `${rounded} ${units[index]}`
}

const formatConstraint = (value, unit) => {
  if (!unit) return String(value)
  if (unit === 'percent') return `${value}%`
  return `${value} ${unit}`
}

const openEditor = (variable) => {
  if (!variable.editable || !variable.edit) return
  saveError.value = ''
  draftValue.value = variable.edit.value
  editing.value = variable
}

const idempotencyKey = (variable) => {
  const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)
  return `database-config-${variable}-${Date.now()}-${random}`
}

const save = async () => {
  if (!editing.value || validationError.value || unchanged.value || saving.value) return
  const variable = editing.value.name
  saving.value = true
  saveError.value = ''
  try {
    const task = await databaseApi.configurations.set(
      variable,
      draftValue.value,
      idempotencyKey(variable),
    )
    editing.value = null
    openTaskDetailPage(router, task.task_id)
  } catch (e) {
    saveError.value = e.message || 'Could not update the database configuration.'
  } finally {
    saving.value = false
  }
}

const load = async () => {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    snapshot.value = await databaseApi.configurations.list()
  } catch (e) {
    snapshot.value = null
    error.value = e.message || 'Could not load database configurations.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <Teleport defer to="#settings-header-actions">
    <Tooltip text="Refresh database configurations">
      <Button
        variant="ghost"
        icon="lucide-refresh-cw"
        :loading="loading"
        aria-label="Refresh database configurations"
        @click="load"
      />
    </Tooltip>
  </Teleport>

  <div v-if="loading && !snapshot" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <template v-else>
  <ErrorMessage v-if="error" :message="error" class="mb-4" />

  <div
    v-if="snapshot && !snapshot.readable"
    class="border border-outline-gray-2 bg-surface-gray-1 px-3 py-2 text-ink-gray-7 text-sm"
  >
    {{ snapshot.reason }}
  </div>

  <template v-else-if="snapshot">
    <div
      v-if="snapshot.edit_reason"
      class="mb-4 border border-outline-gray-2 bg-surface-gray-1 px-3 py-2 text-ink-gray-7 text-sm"
    >
      {{ snapshot.edit_reason }}
    </div>

    <TextInput
      v-model="search"
      placeholder="Search variables"
      autocomplete="off"
      class="mb-5"
    />

    <div v-if="groups.length" class="space-y-7">
      <section v-for="group in groups" :key="group.name">
        <h4 class="mb-1 font-medium text-ink-gray-6 text-sm">
          {{ group.name }}
        </h4>

        <div class="divide-y divide-outline-alpha-gray-1">
          <div
            v-for="variable in group.variables"
            :key="variable.name"
            class="flex sm:flex-row sm:items-center sm:justify-between flex-col gap-3 py-3"
          >
            <code class="min-w-0 font-medium text-ink-gray-8 break-all">
              {{ variable.name }}
            </code>

            <div class="flex items-center justify-between sm:justify-end gap-3 sm:ml-6 shrink-0">
              <span
                class="max-w-48 text-right text-ink-gray-8 text-sm font-mono break-all"
                :class="{ 'text-ink-gray-5': !variable.supported }"
              >
                {{ formatValue(variable) }}
              </span>

              <Button
                v-if="variable.editable"
                variant="ghost"
                icon="lucide-pencil"
                :disabled="saving"
                aria-label="Edit"
                @click="openEditor(variable)"
              />
              <Tooltip v-else :text="variable.reason || 'Read-only in Pilot'">
                <span
                  class="block size-4 text-ink-gray-4 lucide-lock"
                  role="img"
                  :aria-label="variable.reason || 'Read-only in Pilot'"
                />
              </Tooltip>
            </div>
          </div>
        </div>
      </section>
    </div>

    <p v-else class="py-10 text-center text-ink-gray-5 text-sm">
      No database variables match this search.
    </p>
  </template>
  </template>

  <Dialog
    v-model="editorOpen"
    :title="editing ? `Update ${editing.name}` : 'Update database configuration'"
    size="sm"
  >
    <div v-if="editing && editor" class="space-y-4">
      <div v-if="editor.value_type === 'boolean'" class="flex items-center justify-between gap-4">
        <p class="font-medium text-ink-gray-8">Enabled</p>
        <Switch
          class="[&_[data-slot='label']]:sr-only [&>div]:!gap-x-0 [&>div]:!py-0"
          :label="editing.name"
          :model-value="Boolean(draftValue)"
          @update:model-value="(value) => (draftValue = value)"
        />
      </div>

      <TextInput
        v-else
        v-model.number="draftValue"
        type="number"
        :label="inputLabel"
        :min="editor.min"
        :max="editor.max"
        :step="editor.step || 1"
        autocomplete="off"
      />

      <div v-if="editor.value_type === 'integer'" class="text-ink-gray-6 text-sm">
        <p v-if="Number.isInteger(editor.recommended)">
          Recommended: {{ formatConstraint(editor.recommended, editor.unit) }}
        </p>

        <p v-if="Number.isInteger(editor.min) && Number.isInteger(editor.max)">
          Allowed: {{ formatConstraint(editor.min, editor.unit) }} to
          {{ formatConstraint(editor.max, editor.unit) }}
        </p>
      </div>

      <p v-if="restartWarning" class="text-ink-orange-6 text-sm">
        {{ restartWarning }}
      </p>

      <ErrorMessage v-if="validationError" :message="validationError" />
      <ErrorMessage v-if="saveError" :message="saveError" />

      <div class="flex justify-end gap-2">
        <Button variant="ghost" :disabled="saving" @click="editorOpen = false"> Cancel </Button>
        <Button
          variant="solid"
          :loading="saving"
          :disabled="Boolean(validationError) || unchanged"
          @click="save"
        >
          {{ restartWarning ? 'Update and restart' : 'Update' }}
        </Button>
      </div>
    </div>
  </Dialog>
</template>
