<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button, Dialog, Dropdown, type DropdownItem, ErrorMessage } from 'frappe-ui'

import ConfigEntryDialog from '@/components/common/ConfigEntryDialog.vue'
import Table from '@/components/common/Table.vue'

import { errorMessage } from '@/utils/error'
import siteConfigKeys from '@/utils/siteConfigKeys.json'

type Config = Record<string, unknown>

const props = defineProps<{
  config: Config
  fileName: string
  title?: string
  description?: string
  hiddenKeys?: string[]
  save: (patch: Config) => Promise<unknown>
  reload: () => Promise<unknown>
}>()

const columns = [
  { label: 'Config name', key: 'label', class: 'w-3/5', cellClass: 'max-w-0 truncate' },
  { label: 'Value', key: 'value', class: 'w-2/5', cellClass: 'max-w-0 truncate' },
  { label: '', key: 'actions', class: 'w-12' },
]

const TITLES = new Map(siteConfigKeys.map((item) => [item.key, item.title]))
const display = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value))

const rows = computed(() =>
  Object.entries(props.config).map(([key, value]) => ({
    name: key,
    key,
    label: TITLES.has(key) ? `${TITLES.get(key)} (${key})` : key,
    value: display(value),
  })),
)

const menuOptions = (row: { key: string }): DropdownItem[] => [
  { label: 'Edit', icon: 'lucide-pencil', onClick: () => openDialog(row.key) },
  {
    label: 'Remove',
    icon: 'lucide-trash-2',
    theme: 'red',
    onClick: () => {
      deleteKey.value = row.key
      deleteError.value = ''
      showDelete.value = true
    },
  },
]

const showDialog = ref(false)
const editKey = ref<string | null>(null)
const refreshing = ref(false)

const openDialog = (key: string | null = null) => {
  editKey.value = key
  showDialog.value = true
}

const showDelete = ref(false)
const deleteKey = ref('')
const deleting = ref(false)
const deleteError = ref('')

const confirmDelete = async () => {
  deleting.value = true
  deleteError.value = ''
  try {
    await props.save({ [deleteKey.value]: null })
    await props.reload()
    showDelete.value = false
  } catch (e) {
    deleteError.value = errorMessage(e, 'Failed to remove.')
  } finally {
    deleting.value = false
  }
}

const refresh = async () => {
  refreshing.value = true
  try {
    await props.reload()
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3">
      <h4 v-if="title" class="text-base-medium text-ink-gray-8">{{ title }}</h4>
      <p v-else-if="description" class="text-ink-gray-5 text-sm">
        {{ description }} <code class="font-mono text-ink-gray-7">{{ fileName }}</code>.
      </p>

      <div class="flex items-center gap-2 ml-auto shrink-0">
        <Button
          variant="ghost"
          :loading="refreshing"
          icon="lucide-refresh-cw"
          label="Refresh"
          tooltip="Refresh"
          @click="refresh"
        />
        <Button @click="openDialog()">
          <template #prefix><span class="size-4 lucide-plus" /></template>
          Add config
        </Button>
      </div>
    </div>

    <div
      v-if="!rows.length"
      class="py-12 border border-dashed rounded-7 border-outline-gray-2 text-ink-gray-5 text-sm text-center"
    >
      No config keys.
    </div>

    <Table v-else :columns="columns" :rows="rows" height="max-h-96">
      <template #label="{ row }">
        <span :title="row.label">{{ row.label }}</span>
      </template>
      <template #value="{ row }">
        <span :title="row.value">{{ row.value }}</span>
      </template>
      <template #actions="{ row }">
        <Dropdown :options="menuOptions(row)">
          <template #default="{ open }">
            <Button
              variant="ghost"
              :active="open"
              icon="lucide-ellipsis"
              label="Config actions"
              tooltip="Actions"
            />
          </template>
        </Dropdown>
      </template>
    </Table>
  </div>

  <ConfigEntryDialog
    v-model="showDialog"
    :config="config"
    :edit-key="editKey"
    :hidden-keys="hiddenKeys ?? []"
    :save="save"
    :reload="reload"
  />

  <Dialog v-model="showDelete" title="Remove config" size="sm">
    <p class="text-ink-gray-7 text-sm">
      Remove <code class="text-ink-gray-9">{{ deleteKey }}</code> from
      <code class="text-ink-gray-9">{{ fileName }}</code>?
    </p>

    <ErrorMessage v-if="deleteError" :message="deleteError" class="mt-3" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showDelete = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="deleting" @click="confirmDelete"
          >Remove</Button
        >
      </div>
    </template>
  </Dialog>
</template>
