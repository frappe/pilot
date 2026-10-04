<script setup lang="ts">
import { Badge, Button, Dialog, Dropdown, ErrorMessage } from 'frappe-ui'
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import Table from '../components/Table.vue'
import { openExternal } from '../external'
import { settleTask, type Store } from '../store'

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store

const FILE_LABELS = {
  database: __('Download Database'),
  'public-file': __('Download Public'),
  'private-file': __('Download Private'),
  site_config: __('Download Config'),
}

const LINK_KEYS = {
  database: 'database',
  'public-file': 'files',
  'private-file': 'private_files',
  site_config: 'site_config',
}

const backups = ref(null)
const error = ref('')
const creating = ref(false)
const deleteTarget = ref(null)
const showDelete = ref(false)
const deleting = ref(false)
const overlayTarget = inject('overlayTarget', 'body')

let gone = false

onBeforeUnmount(() => (gone = true))

const load = async () => {
  error.value = ''

  try {
    backups.value = await store.api.getBackups()
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

const loadFailed = computed(() => Boolean(error.value) && !backups.value)

const formatDate = (value) =>
  new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })

const columns = [
  { label: __('Date'), key: 'date', class: 'w-1/3' },
  { label: __('Database'), key: 'database', class: 'tabular-nums' },
  { label: __('Public'), key: 'public', class: 'tabular-nums' },
  { label: __('Private'), key: 'private', class: 'tabular-nums' },
  { label: __('Offsite'), key: 'offsite', class: 'text-center' },
  { label: '', key: 'actions', class: 'w-12' },
]

const formatSize = (bytes) => {
  if (!bytes) return '-'

  return bytes < 1024 ** 2
    ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

const fileSize = (backup, kind) =>
  formatSize(backup.files.find((file) => file.kind === kind)?.size_bytes)

const rows = computed(() =>
  (backups.value || []).map((backup) => ({
    id: backup.timestamp,
    date: formatDate(backup.created_at),
    database: fileSize(backup, 'database'),
    public: fileSize(backup, 'public-file'),
    private: fileSize(backup, 'private-file'),
    backup,
  })),
)

const backUp = async () => {
  creating.value = true
  error.value = ''

  try {
    const { task_id } = await store.api.createBackup()

    if (!(await settleTask(task_id, () => gone, __("Couldn't back up the site.")))) return

    await load()

    frappe.show_alert({ message: __('Backup done.'), indicator: 'green' })
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  } finally {
    creating.value = false
  }
}

const download = async (backup, file) => {
  error.value = ''

  if (!backup.is_offsite) return openExternal(`/backups/${encodeURIComponent(file.filename)}`)

  try {
    const links = await store.api.getBackupDownloadLinks(backup.timestamp)
    const url = links[LINK_KEYS[file.kind]]

    if (!url) throw new Error(__('This file is not in the offsite backup.'))

    openExternal(url)
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  }
}

const askDelete = (backup) => {
  deleteTarget.value = backup
  showDelete.value = true
}

const confirmDelete = async () => {
  showDelete.value = false
  deleting.value = true
  error.value = ''

  try {
    const { task_id } = await store.api.deleteBackup(deleteTarget.value.timestamp)

    if (!(await settleTask(task_id, () => gone, __("Couldn't delete the backup.")))) return

    await load()

    frappe.show_alert({ message: __('Backup deleted.'), indicator: 'green' })
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  } finally {
    deleting.value = false
  }
}

const menuOptions = (backup) => [
  ...backup.files.map((file) => ({
    label: FILE_LABELS[file.kind] || file.kind,
    icon: 'lucide-download',
    onClick: () => download(backup, file),
  })),
  {
    label: __('Delete backup'),
    icon: 'lucide-trash-2',
    theme: 'red',
    onClick: () => askDelete(backup),
  },
]
</script>

<template>
  <Panel
    :title="__('Backups')"
    :description="__('Back up your site and download past backups.')"
    :loading="!backups && !error"
    :error="loadFailed ? error : ''"
    :error-title="__(`Couldn't load backups`)"
    @retry="load"
  >
    <template #actions>
      <Button
        class="col-start-2 row-span-2 row-start-1"
        icon-left="lucide-archive"
        :loading="creating"
        :label="creating ? __('Backing up') : __('Back up now')"
        @click="backUp"
      />
    </template>

    <ErrorMessage :message="loadFailed ? '' : error" class="mb-4" />

    <p v-if="!backups.length" class="py-12 text-center text-p-sm text-ink-gray-5">
      {{ __("No backups yet.") }}
    </p>

    <Table v-else :columns="columns" :rows="rows">
      <template #offsite="{ row }">
        <Badge v-if="row.backup.is_offsite" theme="green" size="sm" :label="__('Uploaded')" />

        <Badge v-else size="sm" :label="__('Local only')" />
      </template>

      <template #actions="{ row }">
        <Dropdown align="end" :portal-to="overlayTarget" :options="menuOptions(row.backup)">
          <Button
            variant="ghost"
            icon="lucide-ellipsis"
            :disabled="deleting && deleteTarget?.timestamp !== row.backup.timestamp"
            :loading="deleting && deleteTarget?.timestamp === row.backup.timestamp"
            :label="__('Actions for {0}', [row.date])"
          />
        </Dropdown>
      </template>
    </Table>
  </Panel>

  <Dialog v-model="showDelete" :title="__('Delete backup')" size="md">
    <template #default>
      <p class="text-p-base text-ink-gray-7">
        {{ __("The backup from {0} will be deleted. This can't be undone.", [
            deleteTarget ? formatDate(deleteTarget.created_at) : '',
          ]) }}
      </p>
    </template>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :label="__('Cancel')" @click="showDelete = false" />

        <Button variant="solid" theme="red" :label="__('Delete')" @click="confirmDelete" />
      </div>
    </template>
  </Dialog>
</template>
