<template>
  <div class="space-y-4 mt-5">
    <div class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3">
      <p class="text-ink-gray-5 text-sm">
        Keys passed to this site's <code class="font-mono text-ink-gray-7">site_config.json</code>.
      </p>
      <div class="flex items-center gap-2 shrink-0">
        <Button size="sm" variant="ghost" :loading="refreshing" icon="lucide-refresh-cw" class="hidden sm:flex"
          @click="refresh" />
        <Button size="sm" variant="ghost" :loading="refreshing" icon-left="lucide-refresh-cw" class="sm:hidden"
          @click="refresh">Refresh</Button>
        <Button size="sm" @click="openDialog()">
          <template #prefix><span class="size-4 lucide-plus" /></template>
          Add config
        </Button>
      </div>
    </div>

    <!-- Config table -->
    <div v-if="!rows.length"
      class="py-12 border border-dashed rounded-xl border-outline-gray-2 text-ink-gray-5 text-sm text-center">
      No config keys.
    </div>
    <ListView v-else :columns="columns" :rows="rows" row-key="name"
      :options="{ selectable: false, showTooltip: false }">
      <template #cell="{ column, row, item }">
        <div v-if="column.key === 'actions'" class="flex justify-end">
          <Dropdown v-if="!row.readonly" :options="menuOptions(row)" placement="left">
            <template #default="{ open }">
              <Button variant="ghost" size="sm" :active="open"><span class="size-4 lucide-ellipsis" /></Button>
            </template>
          </Dropdown>
        </div>
        <ListRowItem v-else :column="column" :row="row" :item="item" :align="column.align" />
      </template>
    </ListView>
  </div>

  <!-- Add dialog -->
  <Dialog v-model="showAddDialog" :options="{ title: 'Add config', size: 'sm' }">
    <template #body-content>
      <div class="space-y-3">
        <div class="space-y-1.5">
          <p class="font-medium text-ink-gray-7 text-sm">Key</p>
          <TextInput v-model="entryKey" placeholder="config_key" class="w-full" />
        </div>
        <div class="space-y-1.5">
          <p class="font-medium text-ink-gray-7 text-sm">Value</p>
          <TextInput v-model="entryValue" placeholder="value" class="w-full" />
        </div>
        <ErrorMessage v-if="dialogError" :message="dialogError" />
      </div>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showAddDialog = false">Cancel</Button>
        <Button variant="solid" :loading="saving" @click="save">Save</Button>
      </div>
    </template>
  </Dialog>

  <!-- Edit dialog -->
  <Dialog v-model="showEditDialog" :options="{ title: `Edit ${entryKey}`, size: 'sm' }">
    <template #body-content>
      <div class="space-y-1.5">
        <p class="font-medium text-ink-gray-7 text-sm">Value</p>
        <TextInput v-model="entryValue" placeholder="value" class="w-full" />
      </div>
      <ErrorMessage v-if="dialogError" :message="dialogError" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showEditDialog = false">Cancel</Button>
        <Button variant="solid" :loading="saving" @click="save">Save</Button>
      </div>
    </template>
  </Dialog>

  <!-- Delete dialog -->
  <Dialog v-model="showDelete" :options="{ title: 'Remove config', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Remove <code class="text-ink-gray-9">{{ deleteKey }}</code> from <code
          class="text-ink-gray-9">site_config.json</code>?
      </p>
      <ErrorMessage v-if="deleteError" :message="deleteError" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showDelete = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="deleting" @click="confirmDelete">Remove</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Dialog, Dropdown, ErrorMessage, ListView, ListRowItem, TextInput } from 'frappe-ui'
import { sitesApi } from '@/api/sites'
import { useSite } from '@/composables/useSite'

const props = defineProps({ siteName: { type: String, required: true } })

const { site, reload } = useSite(props.siteName)

const columns = [
  { label: 'Key', key: 'key', align: 'left', width: 2 },
  { label: 'Value', key: 'value', align: 'left', width: 3 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const isPassword = (key) => /password|secret|token|key/i.test(key)

const rows = computed(() => {
  const config = site.value?.site_config || {}
  const entries = Object.entries(config).map(([key, val]) => ({
    name: key,
    key,
    value: isPassword(key) ? '•••••••' : (typeof val === 'string' ? val : JSON.stringify(val)),
    readonly: false,
  }))
  if (site.value?.db_name) {
    entries.unshift({ name: '__db_name', key: 'db_name', value: site.value.db_name, readonly: true })
  }
  return entries
})

function menuOptions(row) {
  return [
    { label: 'Edit', icon: 'lucide-pencil', onClick: () => openDialog(row.key) },
    { label: 'Remove', icon: 'lucide-trash-2', theme: 'red', onClick: () => { deleteKey.value = row.key; deleteError.value = ''; showDelete.value = true } },
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

function openDialog(key = null) {
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

function parseValue(raw) {
  try { return JSON.parse(raw) } catch { return raw }
}

async function save() {
  const key = entryKey.value.trim()
  if (!key) { dialogError.value = 'Key is required.'; return }
  if (isNew.value && key in (site.value.site_config || {})) {
    dialogError.value = 'Key already exists.'; return
  }
  saving.value = true
  dialogError.value = ''
  try {
    await sitesApi.config(props.siteName, { ...site.value.site_config, [key]: parseValue(entryValue.value) })
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

async function confirmDelete() {
  deleting.value = true
  deleteError.value = ''
  try {
    const next = { ...site.value.site_config }
    delete next[deleteKey.value]
    await sitesApi.config(props.siteName, next)
    await reload()
    showDelete.value = false
  } catch (e) {
    deleteError.value = e.message || 'Failed to remove.'
  } finally {
    deleting.value = false
  }
}

async function refresh() {
  refreshing.value = true
  try { await reload() } finally { refreshing.value = false }
}
</script>
