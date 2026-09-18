<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button, Dialog, Dropdown, ErrorMessage, TextInput } from 'frappe-ui'

import Table from '@/components/common/Table.vue'

import { sitesApi } from '@/api/sites'
import { useSite } from '@/composables/sites/useSite'

interface Props {
  siteName: string
}

const props = defineProps<Props>()

const { site, reload } = useSite(props.siteName)

const columns = [
  { label: 'Key', key: 'key', class: 'w-2/5' },
  { label: 'Value', key: 'value' },
  { label: '', key: 'actions', class: 'w-12' },
]

const isPassword = (key) => /password|secret|token|key/i.test(key)

const rows = computed(() => {
  const config = site.value?.site_config || {}
  const entries = Object.entries(config).map(([key, val]) => ({
    name: key,
    key,
    value: isPassword(key) ? '•••••••' : typeof val === 'string' ? val : JSON.stringify(val),
    readonly: false,
  }))
  return entries
})

const menuOptions = (row) => {
  return [
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
}

const showAddDialog = ref(false)
const showEditDialog = ref(false)
const entryKey = ref('')
const entryValue = ref('')
const saving = ref(false)
const dialogError = ref('')
const refreshing = ref(false)
const isNew = computed(() => showAddDialog.value)

const openDialog = (key = null) => {
  dialogError.value = ''
  entryKey.value = key || ''
  if (key !== null) {
    const val = site.value.site_config[key]
    entryValue.value = typeof val === 'string' ? val : JSON.stringify(val)
    showEditDialog.value = true
  } else {
    entryValue.value = ''
    showAddDialog.value = true
  }
}

const parseValue = (raw) => {
  try {
    return JSON.parse(raw)
  } catch {
    return raw
  }
}

const save = async () => {
  const key = entryKey.value.trim()
  if (!key) {
    dialogError.value = 'Key is required.'
    return
  }
  if (isNew.value && key in (site.value.site_config || {})) {
    dialogError.value = 'Key already exists.'
    return
  }
  saving.value = true
  dialogError.value = ''
  try {
    await sitesApi.configuration.update(props.siteName, { [key]: parseValue(entryValue.value) })
    await reload()
    showAddDialog.value = false
    showEditDialog.value = false
  } catch (e) {
    dialogError.value = e.message || 'Failed to save.'
  } finally {
    saving.value = false
  }
}

const showDelete = ref(false)
const deleteKey = ref('')
const deleting = ref(false)
const deleteError = ref('')

const confirmDelete = async () => {
  deleting.value = true
  deleteError.value = ''
  try {
    await sitesApi.configuration.update(props.siteName, { [deleteKey.value]: null })
    await reload()
    showDelete.value = false
  } catch (e) {
    deleteError.value = e.message || 'Failed to remove.'
  } finally {
    deleting.value = false
  }
}

const refresh = async () => {
  refreshing.value = true
  try {
    await reload()
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3">
      <p class="text-ink-gray-5 text-sm">
        Keys passed to this site's <code class="font-mono text-ink-gray-7">site_config.json</code>.
      </p>

      <div class="flex items-center gap-2 shrink-0">
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

    <!-- Config table -->
    <div
      v-if="!rows.length"
      class="py-12 border border-dashed rounded-7 border-outline-gray-2 text-ink-gray-5 text-sm text-center"
    >
      No config keys.
    </div>

    <Table v-else :columns="columns" :rows="rows" height="max-h-96">
      <template #actions="{ row }">
        <Dropdown v-if="!row.readonly" :options="menuOptions(row)">
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

  <Dialog v-model="showAddDialog" title="Add config" size="sm">
    <div class="space-y-3">
      <TextInput label="Key" v-model="entryKey" placeholder="config_key" class="w-full" />
      <TextInput label="Value" v-model="entryValue" placeholder="value" class="w-full" />
      <ErrorMessage v-if="dialogError" :message="dialogError" />
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showAddDialog = false">Cancel</Button>
        <Button variant="solid" :loading="saving" @click="save">Save</Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showEditDialog" :title="`Edit ${entryKey}`" size="sm">
    <TextInput label="Value" v-model="entryValue" placeholder="value" class="w-full" />

    <ErrorMessage v-if="dialogError" :message="dialogError" class="mt-2" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showEditDialog = false">Cancel</Button>
        <Button variant="solid" :loading="saving" @click="save">Save</Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showDelete" title="Remove config" size="sm">
    <p class="text-ink-gray-7 text-sm">
      Remove <code class="text-ink-gray-9">{{ deleteKey }}</code> from
      <code class="text-ink-gray-9">site_config.json</code>?
    </p>

    <ErrorMessage v-if="deleteError" :message="deleteError" class="mt-2" />
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
