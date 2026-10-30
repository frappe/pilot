<script setup lang="ts">
import {
  Alert,
  Button,
  Dialog,
  ErrorMessage,
  Spinner,
  Switch,
  TabButtons,
  TextInput,
  Tooltip,
} from 'frappe-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { databaseApi } from '@/api/database'
import Scrollbar from '@/components/common/Scrollbar.vue'
import type {
  DatabaseConfigurationSnapshot,
  DatabaseVariable,
  MariaDBVariableDoc,
} from '@/types/database'
import { errorMessage } from '@/utils/error'
import { openTaskDetailPage } from '@/utils/taskRoute'

const router = useRouter()

const loading = ref(false)
const docs = ref<Record<string, MariaDBVariableDoc>>({})
const snapshot = ref<DatabaseConfigurationSnapshot | null>(null)
const error = ref('')
const search = ref('')
const selectedSection = ref('')
const editing = ref<DatabaseVariable | null>(null)
const draftValue = ref<DatabaseVariable['value']>(null)
const saving = ref(false)
const saveError = ref('')
const isSnapshotIsolation = computed(() => editing.value?.name === 'innodb_snapshot_isolation')

// TextInput takes strings and numbers only, while the draft also holds booleans.
const draftNumber = computed<string | number>({
  get: () =>
    typeof draftValue.value === 'boolean' || draftValue.value === null ? '' : draftValue.value,
  set: (value) => (draftValue.value = value),
})

const groups = ['InnoDB', 'Replication and Binlog', 'Performance Schema', 'Others']

const groupOf = (variable: DatabaseVariable) => {
  const group = docs.value[variable.name]?.group ?? variable.section

  return groups.includes(group) ? group : 'Others'
}

const sectionNames = computed(() =>
  groups.filter((group) =>
    (snapshot.value?.variables || []).some((variable) => groupOf(variable) === group),
  ),
)

const sectionOptions = computed(() =>
  sectionNames.value.map((name) => ({ label: name, value: name })),
)

const section = computed({
  get: () =>
    sectionNames.value.includes(selectedSection.value)
      ? selectedSection.value
      : (sectionNames.value[0] ?? ''),
  set: (name: string) => {
    selectedSection.value = name
  },
})

const matches = (variable: DatabaseVariable, query: string) =>
  variable.name.toLowerCase().includes(query.trim().toLowerCase())

watch(search, (query) => {
  if (!query.trim()) return

  const variables = snapshot.value?.variables || []
  const withMatches = ['Others', ...sectionNames.value].find((group) =>
    variables.some((variable) => groupOf(variable) === group && matches(variable, query)),
  )

  if (withMatches) section.value = withMatches
})

const rows = computed(() =>
  (snapshot.value?.variables || []).filter(
    (variable) => groupOf(variable) === section.value && matches(variable, search.value),
  ),
)

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

  const draft = draftValue.value
  const { min, max } = editor.value

  if (typeof draft !== 'number' || !Number.isInteger(draft)) return 'Enter a whole number.'
  if (typeof min === 'number' && Number.isInteger(min) && draft < min) {
    return `Enter a value between ${min} and ${max}.`
  }
  if (typeof max === 'number' && Number.isInteger(max) && draft > max) {
    return `Enter a value between ${min} and ${max}.`
  }
  return ''
})
const unchanged = computed(() => editor.value !== null && draftValue.value === editor.value.value)
const restartWarning = computed(() => {
  if (!editor.value) return ''
  if (editor.value.action === 'performance_schema') {
    return 'MariaDB will restart to apply this change.'
  }
  const dynamicMax = editor.value.dynamic_max

  if (
    editor.value.action === 'innodb_buffer_pool_size' &&
    typeof dynamicMax === 'number' &&
    Number.isInteger(dynamicMax) &&
    typeof draftValue.value === 'number' &&
    draftValue.value > dynamicMax
  ) {
    return `MariaDB will restart because this value is above its current live Buffer Pool ceiling of ${formatConstraint(dynamicMax, editor.value.unit)}.`
  }
  return editor.value.requires_restart ? 'MariaDB will restart to apply this change.' : ''
})

const formatValue = (variable: DatabaseVariable) => {
  if (!variable.supported || variable.value === null) return 'Unavailable'
  if (variable.value_type === 'boolean') return variable.value ? 'Enabled' : 'Disabled'
  if (variable.unit === 'bytes' && typeof variable.value === 'number') {
    return Number.isFinite(variable.value) ? formatBytes(variable.value) : 'Unavailable'
  }
  return formatConstraint(variable.value, variable.unit)
}

const formatBytes = (value: number) => {
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

const formatConstraint = (value: DatabaseVariable['value'], unit: string) => {
  if (!unit) return String(value)
  if (unit === 'percent') return `${value}%`
  if (unit === 'seconds') return `${value}s`
  if (unit === 'milliseconds') return `${value}ms`

  return `${value} ${unit}`
}

const formatDefault = (variable: DatabaseVariable) => {
  const doc = docs.value[variable.name]

  if (!doc || doc.default === null) return doc?.default_note ? 'Varies' : '-'

  return formatValue({ ...variable, value: doc.default, supported: true })
}

const isChanged = (variable: DatabaseVariable) => {
  const doc = docs.value[variable.name]

  if (!doc || doc.default === null || doc.default_note || variable.value === null) return false
  if (typeof doc.default === 'string') {
    return String(variable.value).toUpperCase() !== doc.default.toUpperCase()
  }

  return Number(variable.value) !== Number(doc.default)
}

const openEditor = (variable: DatabaseVariable) => {
  if (!variable.editable || !variable.edit) return
  saveError.value = ''
  draftValue.value = variable.edit.value
  editing.value = variable
}

const idempotencyKey = (variable: string) => {
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
    saveError.value = errorMessage(e, 'Could not update the database configuration.')
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
    error.value = errorMessage(e, 'Could not load database configurations.')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  load()

  const { default: variables } = await import('@/data/mariadbVariables.json')

  docs.value = variables
})
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

  <div v-else class="flex flex-col flex-1 gap-4 min-h-0">
    <ErrorMessage v-if="error" :message="error" />

    <Alert v-if="snapshot && !snapshot.readable" :title="snapshot.reason" :dismissible="false" />

    <template v-else-if="snapshot">
      <Alert v-if="snapshot.edit_reason" :title="snapshot.edit_reason" :dismissible="false" />

      <div class="flex justify-between items-center gap-3 shrink-0">
        <TextInput
          v-model="search"
          class="w-44 shrink-0"
          placeholder="Search"
          aria-label="Search variables"
          autocomplete="off"
        >
          <template #prefix>
            <span class="size-4 text-ink-gray-4 lucide-search" aria-hidden="true" />
          </template>
        </TextInput>

        <div class="min-w-0 overflow-x-auto [scrollbar-width:none]">
          <TabButtons v-model="section" :options="sectionOptions" class="w-max" />
        </div>
      </div>

      <Scrollbar class="flex-1 -mr-4 min-h-0">
        <div
          class="top-0 z-10 sticky flex items-center gap-3 bg-surface-elevation-1 mr-4 border-b border-outline-gray-2 h-8 text-ink-gray-5 text-sm"
        >
          <span>Variable</span>
          <span class="ml-auto w-24 text-right">Default</span>
          <span class="w-28 text-right">Value</span>
          <span v-if="snapshot.editable" class="w-7" />
        </div>

        <div class="mr-4 divide-y divide-outline-alpha-gray-1">
          <div v-for="variable in rows" :key="variable.name" class="flex items-center gap-3 h-10">
            <code class="min-w-0 font-mono text-ink-gray-8 text-sm truncate">
              {{ variable.name }}
            </code>

            <span
              class="ml-auto w-24 text-ink-gray-5 text-sm text-right truncate tabular-nums shrink-0"
              :title="docs[variable.name]?.default_note"
            >
              {{ formatDefault(variable) }}
            </span>

            <span
              class="flex justify-end items-center gap-1.5 w-28 text-sm tabular-nums shrink-0"
              :class="variable.supported ? 'text-ink-gray-8' : 'text-ink-gray-5'"
            >
              <span
                v-if="isChanged(variable)"
                class="bg-surface-blue-5 rounded-full size-1.5 shrink-0"
                role="img"
                aria-label="Changed from default"
              />
              <span class="truncate">{{ formatValue(variable) }}</span>
            </span>

            <Button
              v-if="variable.editable"
              class="!text-ink-gray-5 hover:!text-ink-gray-7 shrink-0"
              variant="ghost"
              icon="lucide-pencil"
              :disabled="saving"
              :aria-label="`Edit ${variable.name}`"
              @click="openEditor(variable)"
            />

            <span v-else-if="snapshot.editable" class="w-7 shrink-0" />
          </div>
        </div>

        <p v-if="!rows.length" class="mr-4 py-10 text-ink-gray-5 text-sm text-center">
          No database variables match this search.
        </p>
      </Scrollbar>
    </template>
  </div>

  <Dialog
    v-model="editorOpen"
    :title="editing ? `Update ${editing.name}` : 'Update database configuration'"
    size="sm"
  >
    <div v-if="editing && editor" class="space-y-4">
      <Alert
        v-if="isSnapshotIsolation"
        theme="amber"
        title="Warning"
        description="Enabling snapshot isolation can cause concurrent updates and deletes to fail and roll back transactions. Disabling it removes this write-conflict protection. The global change applies to new database connections; existing connections retain their session setting."
      />
      <div v-if="editor.value_type === 'boolean'" class="flex items-center justify-between gap-4">
        <p class="text-base-medium text-ink-gray-8">Enabled</p>
        <Switch
          class="[&_[data-slot='label']]:sr-only [&>div]:!gap-x-0 [&>div]:!py-0"
          :label="editing.name"
          :model-value="Boolean(draftValue)"
          @update:model-value="(value) => (draftValue = value)"
        />
      </div>

      <TextInput
        v-else
        v-model.number="draftNumber"
        type="number"
        :label="inputLabel"
        required
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
