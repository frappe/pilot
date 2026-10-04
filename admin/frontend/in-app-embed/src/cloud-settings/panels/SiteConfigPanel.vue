<script setup lang="ts">
import { Button, Dialog, Dropdown, ErrorMessage, TextInput } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import Table from '../components/Table.vue'
import type { Store } from '../store'

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store

const columns = [
  { label: __('Key'), key: 'key', class: 'w-1/3' },
  { label: __('Value'), key: 'value' },
  { label: '', key: 'actions', class: 'w-12' },
]

const config = ref(null)
const error = ref('')
const saving = ref(false)
const showEdit = ref(false)
const editing = ref(false)
const draftKey = ref('')
const draftValue = ref('')
const removeTarget = ref('')

const load = async () => {
  error.value = ''

  try {
    config.value = await store.api.getSiteConfig()
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  }
}

watch(
  () => props.active,
  (active) => {
    if (active) load()
  },
  { immediate: true },
)

const formatValue = (value) => (typeof value === 'string' ? value : JSON.stringify(value))

const rows = computed(() =>
  Object.entries(config.value || {})
    .map(([key, value]) => ({ id: key, key, value: formatValue(value) }))
    .sort((a, b) => a.key.localeCompare(b.key)),
)

const parseValue = (text) => {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

const openEdit = (row) => {
  editing.value = Boolean(row)
  draftKey.value = row?.key || ''
  draftValue.value = row?.value || ''
  error.value = ''
  showEdit.value = true
}

const save = async (patch) => {
  saving.value = true
  error.value = ''

  try {
    config.value = await store.api.updateSiteConfig(patch)

    return true
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)

    return false
  } finally {
    saving.value = false
  }
}

const saveDraft = async () => {
  const key = draftKey.value.trim()

  if (!key) return

  if (await save({ [key]: parseValue(draftValue.value.trim()) })) showEdit.value = false
}

const confirmRemove = async () => {
  const key = removeTarget.value

  removeTarget.value = ''

  await save({ [key]: null })
}

const menuOptions = (row) => [
  { label: __('Edit'), icon: 'lucide-pencil', onClick: () => openEdit(row) },
  {
    label: __('Remove'),
    icon: 'lucide-trash-2',
    theme: 'red',
    onClick: () => (removeTarget.value = row.key),
  },
]
</script>

<template>
  <Panel
    :title="__('Site config')"
    :description="__('Settings your site reads from site_config.json.')"
    :loading="!config && !error"
    :error="config ? '' : error"
    :error-title="__(`Couldn't load site config`)"
    @retry="load"
  >
    <template #actions>
      <Button
        class="col-start-2 row-span-2 row-start-1"
        icon-left="lucide-plus"
        :label="__('Add key')"
        @click="openEdit(null)"
      />
    </template>

    <ErrorMessage :message="showEdit ? '' : error" class="mb-4" />

    <p v-if="!rows.length" class="py-12 text-center text-p-sm text-ink-gray-5">
      {{ __("No config keys yet.") }}
    </p>

    <Table v-else :columns="columns" :rows="rows">
      <template #key="{ row }">
        <span class="font-mono text-sm text-ink-gray-8">{{ row.key }}</span>
      </template>

      <template #value="{ row }">
        <span class="block max-w-80 truncate font-mono text-sm text-ink-gray-6" :title="row.value">
          {{ row.value }}
        </span>
      </template>

      <template #actions="{ row }">
        <Dropdown align="end" :options="menuOptions(row)">
          <Button
            variant="ghost"
            icon="lucide-ellipsis"
            :disabled="saving"
            :label="__('Actions for {0}', [row.key])"
          />
        </Dropdown>
      </template>
    </Table>
  </Panel>

  <Dialog v-model="showEdit" :title="editing ? __('Edit key') : __('Add key')" size="md">
    <template #default>
      <div class="space-y-4">
        <TextInput
          v-model="draftKey"
          :label="__('Key')"
          :placeholder="__('maintenance_mode')"
          :disabled="editing"
        />

        <TextInput
          v-model="draftValue"
          :label="__('Value')"
          :placeholder="__('1')"
          @keydown.enter="saveDraft"
        />

        <p class="text-p-sm text-ink-gray-5">
          {{ __("Numbers, true, false and JSON are saved as-is. Anything else is saved as text.") }}
        </p>

        <ErrorMessage :message="error" />
      </div>
    </template>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :label="__('Cancel')" @click="showEdit = false" />

        <Button
          variant="solid"
          :disabled="!draftKey.trim()"
          :loading="saving"
          :label="__('Save')"
          @click="saveDraft"
        />
      </div>
    </template>
  </Dialog>

  <Dialog
    :model-value="Boolean(removeTarget)"
    :title="__('Remove key')"
    size="md"
    @update:model-value="(open) => !open && (removeTarget = '')"
  >
    <template #default>
      <p class="text-p-base text-ink-gray-7">
        {{ __("{0} will be removed from the site config.", [removeTarget]) }}
      </p>
    </template>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :label="__('Cancel')" @click="removeTarget = ''" />

        <Button variant="solid" theme="red" :label="__('Remove')" @click="confirmRemove" />
      </div>
    </template>
  </Dialog>
</template>
