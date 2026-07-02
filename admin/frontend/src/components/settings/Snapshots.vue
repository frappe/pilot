<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Alert v-if="!snapshotsEnabled" title="ZFS not ready" theme="yellow" :dismissible="false">
      <template #description>
        <span class="text-ink-gray-6 text-p-sm">
          ZFS storage is enabled in settings but the pool isn't ready yet. Snapshots will be available once the
          bench has finished setting up its dataset.
        </span>
      </template>
    </Alert>

    <div class="space-y-3">
      <p class="font-medium text-ink-gray-8 text-base leading-normal">Storage</p>
      <div class="gap-3 grid grid-cols-2">
        <div class="space-y-1.5">
          <p class="font-medium text-ink-gray-7 text-sm">Reservation</p>
          <TextInput v-model="reservation" placeholder="5G" class="w-full" />
        </div>
        <div class="space-y-1.5">
          <p class="font-medium text-ink-gray-7 text-sm">Quota</p>
          <TextInput v-model="quota" placeholder="50G" class="w-full" />
        </div>
      </div>
      <ErrorMessage v-if="volumeError" :message="volumeError" />
      <div class="flex justify-end">
        <Button variant="solid" :loading="savingVolume" @click="saveVolume">Save changes</Button>
      </div>
    </div>

    <div class="space-y-2">
      <div class="flex justify-between items-center">
        <p class="font-medium text-ink-gray-8 text-base leading-normal">Snapshots</p>
        <Button variant="subtle" icon-left="plus" :disabled="!snapshotsEnabled" :loading="creating"
          @click="createSnapshot">Create snapshot</Button>
      </div>

      <div v-if="!snapshots.length"
        class="flex flex-col items-center gap-2.5 py-10 border border-dashed rounded-lg border-outline-gray-2 text-center">
        <div class="flex justify-center items-center bg-surface-gray-2 rounded-full size-11">
          <span class="size-5 text-ink-gray-5 lucide-camera"></span>
        </div>
        <p class="font-medium text-ink-gray-7 text-sm">No snapshots yet</p>
        <p class="max-w-xs text-ink-gray-5 text-xs">
          Snapshots capture the bench's dataset (files and database together) so you can roll back instantly.
        </p>
      </div>

      <div v-else class="space-y-3">
        <div v-for="(snap, index) in snapshots" :key="snap.tag" class="flex items-end gap-2">
          <div class="flex-1 space-y-1.5 min-w-0">
            <p v-if="index === 0" class="font-medium text-ink-gray-7 text-sm">Tag</p>
            <p class="text-ink-gray-8 text-sm truncate">{{ snap.tag }}</p>
          </div>
          <div class="flex-1 space-y-1.5">
            <p v-if="index === 0" class="font-medium text-ink-gray-7 text-sm">Created</p>
            <p class="text-ink-gray-8 text-sm">{{ fmt(snap.created_at) }}</p>
          </div>
          <div class="w-24 space-y-1.5 shrink-0">
            <p v-if="index === 0" class="font-medium text-ink-gray-7 text-sm">Size</p>
            <p class="text-ink-gray-8 text-sm">{{ fmtSize(snap.used_bytes) }}</p>
          </div>
          <Button variant="subtle" icon="lucide-history" @click="openRollback(snap)" />
          <Button variant="subtle" icon="lucide-x" @click="openDelete(snap)" />
        </div>
      </div>

      <ErrorMessage v-if="snapshotError" :message="snapshotError" />
    </div>
  </div>

  <!-- Rollback confirmation -->
  <Dialog v-model="showRollback" :options="{ title: 'Rollback snapshot', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Roll back to <strong>{{ rollbackTarget?.tag }}</strong>? Every change made since this snapshot was taken
        will be lost. This cannot be undone.
      </p>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showRollback = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="rollingBack" @click="confirmRollback">Rollback</Button>
      </div>
    </template>
  </Dialog>

  <!-- Delete confirmation -->
  <Dialog v-model="showDelete" :options="{ title: 'Delete snapshot', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Delete <strong>{{ deleteTarget?.tag }}</strong>? This cannot be undone.
      </p>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showDelete = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="deleting" @click="confirmDelete">Delete</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Alert, Button, Dialog, ErrorMessage, TextInput, toast } from 'frappe-ui'
import { settingsApi } from '@/api/settings'
import { volumeApi } from '@/api/volume'

const loading = ref(true)
const reservation = ref('')
const quota = ref('')
const savingVolume = ref(false)
const volumeError = ref('')

const snapshotsEnabled = ref(false)
const snapshots = ref([])
const creating = ref(false)
const snapshotError = ref('')

const showRollback = ref(false)
const rollbackTarget = ref(null)
const rollingBack = ref(false)

const showDelete = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)

const fmt = (iso) => new Date(iso).toLocaleString()
const fmtSize = (b) => !b ? '—' : b < 1024 ** 3 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${(b / 1024 ** 3).toFixed(1)} GB`

function openRollback(snap) {
  rollbackTarget.value = snap
  showRollback.value = true
}

function openDelete(snap) {
  deleteTarget.value = snap
  showDelete.value = true
}

async function loadVolumeSettings() {
  const data = await settingsApi.get()
  const volume = data.volume || {}
  reservation.value = volume.reservation || ''
  quota.value = volume.quota || ''
}

async function loadSnapshots() {
  snapshotError.value = ''
  try {
    const data = await volumeApi.snapshots.list()
    if (data.error) {
      snapshotsEnabled.value = false
      snapshots.value = []
      return
    }
    snapshotsEnabled.value = !!data.snapshots_enabled
    snapshots.value = data.snapshots || []
  } catch (e) {
    snapshotsEnabled.value = false
    snapshotError.value = e.message || 'Failed to load snapshots.'
  }
}

async function saveVolume() {
  volumeError.value = ''
  savingVolume.value = true
  try {
    const result = await settingsApi.update({ volume: { reservation: reservation.value.trim(), quota: quota.value.trim() } })
    if (result.ok) {
      toast.success('Storage settings saved')
      if (result.zfs_error) toast.error(result.zfs_error)
    } else {
      volumeError.value = result.error || 'Failed to save.'
    }
  } catch (e) {
    volumeError.value = e.message || 'Failed to save.'
  } finally {
    savingVolume.value = false
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
  try {
    await Promise.all([loadVolumeSettings(), loadSnapshots()])
  } finally {
    loading.value = false
  }
})
</script>
