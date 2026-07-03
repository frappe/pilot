<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Teleport to="#settings-header-actions">
      <div class="flex items-center gap-2">
        <CronScheduleControl ref="cronControlRef" noun="snapshots"
          disable-body="Automatic snapshots will stop. Existing snapshots are kept."
          :fetch-schedule="fetchSnapshotSchedule" :set-schedule="setSnapshotSchedule"
          :remove-schedule="removeSnapshotSchedule" titleless />
        <Button class="hidden sm:inline-flex" variant="subtle" icon-left="camera" :loading="creating"
          @click="createSnapshot">Snapshot</Button>
      </div>
    </Teleport>

    <div class="space-y-2">
      <div v-if="!snapshots.length"
        class="flex flex-col items-center gap-2.5 py-10 border border-dashed rounded-lg border-outline-gray-2 text-center">
        <div class="flex justify-center items-center bg-surface-gray-2 rounded-full size-11">
          <span class="size-5 text-ink-gray-5 lucide-camera"></span>
        </div>
        <p class="font-medium text-ink-gray-7 text-sm">No snapshots yet</p>
        <p class="max-w-xs text-ink-gray-5 text-xs text-balance">
          Snapshots capture your bench's files and database so you can roll back instantly.
        </p>
      </div>

      <ListView v-else :columns="columns" :rows="rows" row-key="tag"
        :options="{ selectable: false, showTooltip: false }">
        <template #cell="{ column, row, item }">
          <div v-if="column.key === 'actions'" class="flex justify-end">
            <Dropdown :options="menuOptions(row.snap)" placement="left">
              <template #default="{ open }">
                <Button variant="ghost" size="sm" :active="open"><span class="size-4 lucide-ellipsis" /></Button>
              </template>
            </Dropdown>
          </div>
          <div v-else-if="column.key === 'offsite'" class="flex justify-center">
            <span v-if="row.snap.is_offsite" class="size-4 text-ink-gray-6 lucide-check" title="Backed up offsite" />
            <span v-else class="size-4 text-ink-gray-4 lucide-x" title="Not backed up offsite" />
          </div>
          <ListRowItem v-else :column="column" :row="row" :item="item" :align="column.align" />
        </template>
      </ListView>

      <ErrorMessage v-if="snapshotError" :message="snapshotError" />
    </div>
  </div>

  <!-- Restore confirmation -->
  <Dialog v-model="showRollback" :options="{ title: 'Restore snapshot', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        Restore <span class="font-semibold text-ink-gray-8 break-all">{{ rollbackTarget?.tag }}</span>?
        Every change made since this snapshot was taken will be lost. This cannot be undone.
      </p>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showRollback = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="rollingBack" @click="confirmRollback">Restore snapshot</Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showCliRestore" :options="{ title: 'Restore from the command line', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        Restoring the offsite snapshot
        <span class="font-semibold text-ink-gray-8 break-all">{{ rollbackTarget?.tag }}</span>
        requires the bench to be stopped, so it can't run from here. Run this on the server:
      </p>
      <code class="mt-2 block rounded bg-surface-gray-2 px-2 py-1.5 font-mono text-sm text-ink-gray-8 select-all whitespace-pre overflow-x-auto"
        >{{ cliRestoreCommands }}</code
      >
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="subtle" icon-left="copy" @click="copyCliRestoreCommands">Copy</Button>
        <Button variant="solid" @click="showCliRestore = false">Done</Button>
      </div>
    </template>
  </Dialog>

  <!-- Delete confirmation -->
  <Dialog v-model="showDelete" :options="{ title: 'Delete snapshot', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        Delete <span class="font-semibold text-ink-gray-8 break-all">{{ deleteTarget?.tag }}</span>?
        This cannot be undone.
      </p>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showDelete = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="deleting" @click="confirmDelete">Delete</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, Dropdown, ErrorMessage, ListRowItem, ListView, toast } from 'frappe-ui'
import CronScheduleControl from '@/components/CronScheduleControl.vue'
import { settingsApi } from '@/api/settings'
import { volumeApi } from '@/api/volume'
import { openTaskDetailPage } from '@/utils/taskRoute'

const router = useRouter()
const emit = defineEmits(['close'])

const fetchSnapshotSchedule = () => volumeApi.snapshots.schedule.get()
const setSnapshotSchedule = (cron) => volumeApi.snapshots.schedule.set(cron)
const removeSnapshotSchedule = () => volumeApi.snapshots.schedule.remove()

const cronControlRef = ref(null)

const columns = [
  { label: 'Created', key: 'created', align: 'left', width: 3 },
  { label: 'Size', key: 'size', align: 'center', width: 1 },
  { label: 'Offsite', key: 'offsite', align: 'center', width: 1 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const loading = ref(true)
const snapshots = ref([])
const creating = ref(false)
const snapshotError = ref('')

const showRollback = ref(false)
const showCliRestore = ref(false)
const benchName = ref('')

const cliRestoreCommands = computed(() => {
  const flag = benchName.value ? ` -b ${benchName.value}` : ''
  const tag = rollbackTarget.value?.tag || '<tag>'
  return `bench${flag} stop\nbench${flag} volume restore-snapshot ${tag}\nbench${flag} start`
})

async function copyCliRestoreCommands() {
  await navigator.clipboard.writeText(cliRestoreCommands.value)
  toast.success('Copied to clipboard')
}
const rollbackTarget = ref(null)
const rollingBack = ref(false)

const showDelete = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)

const fmt = (iso) => new Date(iso).toLocaleString()
const fmtSize = (b) => !b ? '—' : b < 1024 ** 3 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${(b / 1024 ** 3).toFixed(1)} GB`

const rows = computed(() => snapshots.value.map((snap) => ({
  tag: snap.tag,
  created: fmt(snap.created_at),
  size: fmtSize(snap.used_bytes),
  snap,
})))

function menuOptions(snap) {
  return [
    ...((snap.is_local || snap.is_offsite || snap.is_downloaded) && !snap.is_uploading ? [{
      label: 'Restore snapshot', icon: 'lucide-history',
      onClick: () => openRollback(snap),
    }] : []),
    ...(!snap.is_uploading ? [{
      label: 'Delete snapshot', icon: 'lucide-trash-2', theme: 'red',
      onClick: () => openDelete(snap),
    }] : []),
  ]
}

function openRollback(snap) {
  rollbackTarget.value = snap
  if (!snap.is_local) {
    showCliRestore.value = true
    return
  }
  showRollback.value = true
}

function openDelete(snap) {
  deleteTarget.value = snap
  showDelete.value = true
}

async function loadBenchName() {
  const data = await settingsApi.get()
  benchName.value = data.bench?.name || ''
}

async function loadSnapshots() {
  snapshotError.value = ''
  try {
    const data = await volumeApi.snapshots.list()
    if (data.error) {
      snapshots.value = []
      snapshotError.value = data.error
      return
    }
    snapshots.value = data.snapshots || []
  } catch (e) {
    snapshotError.value = e.message || 'Failed to load snapshots.'
  }
}

async function createSnapshot() {
  creating.value = true
  snapshotError.value = ''
  try {
    const result = await volumeApi.snapshots.create()
    if (result.error) {
      snapshotError.value = result.error
      return
    }
    if (result.task_id) {
      emit('close')
      openTaskDetailPage(router, result.task_id)
      return
    }
    toast.success(`Snapshot ${result.tag} created`)
    await loadSnapshots()
  } catch (e) {
    snapshotError.value = e.message || 'Failed to create snapshot.'
  } finally {
    creating.value = false
  }
}

async function confirmRollback() {
  rollingBack.value = true
  try {
    const result = await volumeApi.snapshots.rollback(rollbackTarget.value.tag)
    if (result.error) {
      toast.error(result.error)
      return
    }
    showRollback.value = false
    toast.success(`Rolled back to ${rollbackTarget.value.tag}`)
    await loadSnapshots()
  } catch (e) {
    toast.error(e.message || 'Failed to rollback.')
  } finally {
    rollingBack.value = false
  }
}

async function confirmDelete() {
  deleting.value = true
  try {
    const result = await volumeApi.snapshots.destroy(deleteTarget.value.tag)
    if (result.error) {
      toast.error(result.error)
      return
    }
    showDelete.value = false
    toast.success('Snapshot deleted')
    await loadSnapshots()
  } catch (e) {
    toast.error(e.message || 'Failed to delete snapshot.')
  } finally {
    deleting.value = false
  }
}

onMounted(async () => {
  loadBenchName()
  try {
    await loadSnapshots()
  } finally {
    loading.value = false
  }
})
</script>
