<script setup lang="ts">
import { useRouter } from 'vue-router'
import { computed, onMounted, ref } from 'vue'
import { Badge, Button, Dialog, Dropdown, ErrorMessage, Select } from 'frappe-ui'

import EmptyState from '@/components/common/EmptyState.vue'
import ListSkeleton from '@/components/common/ListSkeleton.vue'
import Table from '@/components/common/Table.vue'
import BackupConfigDialog from '@/components/sites/BackupConfigDialog.vue'

import { sitesApi } from '@/api/sites'
import { tasksApi } from '@/api/tasks'
import { cronToLabel } from '@/utils/backup'
import { apiErrorMessage } from '@/api/client'
import { fmtDateTime } from '@/utils/taskFormat'
import { useSite } from '@/composables/sites/useSite'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  siteName: string
}

const props = defineProps<Props>()
const router = useRouter()

const {
  backups,
  backupsLoading,
  backupsHasMore,
  backupsLimit,
  loadBackups,
  loadMoreBackups,
  setBackupsPageLength,
} = useSite(props.siteName)

const pageLengths = [20, 50, 100].map((n) => ({ label: `${n} per page`, value: n }))

const backingUp = ref(false)
const error = ref('')

const configRef = ref(null)
const config = ref(null)
const enabled = computed(() => !!config.value?.schedule)

const scheduleSummary = computed(() =>
  enabled.value
    ? `${cronToLabel(config.value.schedule)}.`
    : 'Manual backups are kept until you delete them.',
)

const loadConfig = async () => {
  try {
    config.value = await sitesApi.backups.schedule.get(props.siteName)
  } catch {
    config.value = null
  }
}

const backupNow = async () => {
  backingUp.value = true
  error.value = ''
  try {
    const result = await sitesApi.backups.create(props.siteName)
    if (result.task_id) openTaskDetailPage(router, result.task_id)
    else error.value = apiErrorMessage(result, 'Backup failed.')
  } catch (e) {
    error.value = e.message || 'Backup failed.'
  } finally {
    backingUp.value = false
  }
}

const columns = [
  { label: 'Date', key: 'timestamp', class: 'w-1/3' },
  { label: 'Database', key: 'database', class: 'tabular-nums' },
  { label: 'Public', key: 'public', class: 'tabular-nums' },
  { label: 'Private', key: 'private', class: 'tabular-nums' },
  { label: 'Offsite', key: 'offsite', class: 'text-center' },
  { label: '', key: 'actions', class: 'w-12' },
]

const fileOf = (set, kind) => set.files?.find((f) => f.kind === kind) ?? null
const fmtSize = (b) =>
  !b ? '-' : b < 1024 ** 2 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 ** 2).toFixed(1)} MB`

const rows = computed(() =>
  backups.value.map((set) => ({
    name: set.created_at,
    timestamp: fmtDateTime(set.created_at),
    database: fmtSize(fileOf(set, 'database')?.size_bytes),
    public: fmtSize(fileOf(set, 'public-file')?.size_bytes),
    private: fmtSize(fileOf(set, 'private-file')?.size_bytes),
    set,
  })),
)

// The offsite metadata's file_type keys don't match the UI's kind names;
// this is the same mapping BackupReader uses to merge remote-only files in.
const OFFSITE_KIND_KEYS = {
  database: 'database',
  'public-file': 'files',
  'private-file': 'private_files',
  site_config: 'site_config',
}

const menuOptions = (set) => {
  const kinds = [
    ['database', 'Download Database'],
    ['public-file', 'Download Public'],
    ['private-file', 'Download Private'],
    ['site_config', 'Download Config'],
  ]
  return [
    ...kinds
      .filter(([k]) => fileOf(set, k))
      .map(([k, label]) => ({
        label,
        icon: 'lucide-download',
        onClick: () => downloadFile(set, k),
      })),
    {
      label: 'Delete backup',
      icon: 'lucide-trash-2',
      theme: 'red',
      onClick: () => {
        deleteTarget.value = set
        showDelete.value = true
      },
    },
  ]
}

const downloadFile = async (set, kind) => {
  const file = fileOf(set, kind)
  if (file?.path) {
    window.location.href = sitesApi.backups.download(props.siteName, set.timestamp, file.filename)
    return
  }
  // Offsite-only file: fetch a direct, time-limited S3 link and open it -
  // this server never proxies or re-downloads the transfer.
  error.value = ''
  try {
    const links = await sitesApi.backups.downloadLinks(props.siteName, set.timestamp)
    if (links.error) {
      error.value = apiErrorMessage(links, 'Could not load offsite backup.')
      return
    }
    const url = links[OFFSITE_KIND_KEYS[kind]]
    if (!url) {
      error.value = 'Backup file not found offsite.'
      return
    }
    window.open(url, '_blank')
  } catch (e) {
    error.value = e.message || 'Failed to get offsite download link.'
  }
}

const showDelete = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)
const deleteError = ref('')

const confirmDelete = async () => {
  deleting.value = true
  deleteError.value = ''
  try {
    const filenames = deleteTarget.value.files.map((f) => f.filename)
    const data = await tasksApi.run('delete-backup', { site: props.siteName, filenames })
    if (data.task_id) {
      showDelete.value = false
      openTaskDetailPage(router, data.task_id)
    } else deleteError.value = apiErrorMessage(data, 'Delete failed.')
  } catch (e) {
    deleteError.value = e.message || 'Delete failed.'
  } finally {
    deleting.value = false
  }
}

onMounted(() => {
  loadBackups()
  loadConfig()
})
</script>

<template>
  <div class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3 mb-4">
    <div>
      <p class="font-medium text-ink-gray-8">Automated backups</p>
      <p class="mt-0.5 text-ink-gray-5 text-p-sm">{{ scheduleSummary }}</p>
    </div>

    <div class="flex items-center gap-2 shrink-0">
      <Button @click="configRef.open()">
        {{ enabled ? 'Configure' : 'Enable' }}
      </Button>
      <Button :loading="backingUp" @click="backupNow">
        <template #prefix><span class="size-4 lucide-archive" /></template>
        Back up now
      </Button>
    </div>
  </div>

  <BackupConfigDialog ref="configRef" :site-name="siteName" @saved="loadConfig" />

  <ErrorMessage v-if="error" :message="error" class="mb-4" />

  <ListSkeleton v-if="backupsLoading" :rows="5" />

  <EmptyState
    v-else-if="!backups.length"
    icon="lucide-archive"
    title="No backups yet"
    :description="
      enabled
        ? 'Automatic backups run on schedule. You can also back up now.'
        : 'Enable automatic backups to start protecting your site.'
    "
  >
    <Button :loading="backingUp" @click="backupNow">
      <template #prefix><span class="size-4 lucide-archive" /></template>
      Back up now
    </Button>
  </EmptyState>

  <template v-else>
    <Table :columns="columns" :rows="rows" height="max-h-[32rem]">
      <template #offsite="{ row }">
        <Badge
          v-if="row.set.is_offsite"
          theme="green"
          size="sm"
          label="Uploaded"
        />
        <Badge v-else theme="gray" size="sm" label="Local only" />
      </template>

      <template #actions="{ row }">
        <Dropdown :options="menuOptions(row.set)">
          <template #default="{ open }">
            <Button
              variant="ghost"
              :active="open"
              icon="lucide-ellipsis"
              label="Backup actions"
              tooltip="Actions"
            />
          </template>
        </Dropdown>
      </template>
    </Table>

    <div v-if="backupsHasMore || backups.length > 20" class="flex items-center gap-3 mt-2 px-1">
      <Select
        size="sm"
        :model-value="backupsLimit"
        :options="pageLengths"
        @update:model-value="setBackupsPageLength"
      />

      <span class="text-ink-gray-5 text-sm">{{ backups.length }} backups</span>

      <Button v-if="backupsHasMore" class="ml-auto" @click="loadMoreBackups">Load more</Button>
    </div>
  </template>

  <Dialog v-model="showDelete" title="Delete Backup" size="sm">
    <p class="text-ink-gray-7 text-sm">
      Delete the backup from
      <strong>{{ deleteTarget ? fmtDateTime(deleteTarget.created_at) : '' }}</strong>? This cannot
      be undone.
    </p>

    <ErrorMessage v-if="deleteError" :message="deleteError" class="mt-2" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showDelete = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="deleting" @click="confirmDelete"
          >Delete</Button
        >
      </div>
    </template>
  </Dialog>
</template>
