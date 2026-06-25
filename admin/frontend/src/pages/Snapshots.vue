<script setup>
import { computed, onMounted, ref } from 'vue'
import { Button, Dialog, ErrorMessage, ListView, LoadingText } from 'frappe-ui'

const allSnapshots = ref([])
const snapshotsEnabled = ref(true)
const loading = ref(false)
const loadError = ref('')
const createError = ref('')
const deletingTag = ref('')
const createLoading = ref(false)
const showRollbackDialog = ref(false)
const rollbackRow = ref(null)
const rollbackLoading = ref(false)
const rollbackError = ref('')

const columns = [
  { label: 'Snapshot Tag', key: 'tag' },
  { label: 'Created', key: 'formattedDate', width: '180px' },
  { label: 'Used', key: 'formattedSize', width: '100px' },
  { label: '', key: '_rollback', width: '90px' },
  { label: '', key: '_delete', width: '80px' },
]

const rows = computed(() =>
  allSnapshots.value.map(snapshot => ({
    ...snapshot,
    formattedDate: formatDate(snapshot.created_at),
    formattedSize: formatBytes(snapshot.used_bytes),
  }))
)

function formatDate(isoString) {
  return new Date(isoString).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function formatBytes(bytes) {
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

async function loadSnapshots() {
  loading.value = true
  loadError.value = ''
  try {
    const response = await fetch('/api/volume/snapshots')
    if (!response.ok) throw new Error(await response.text())
    const data = await response.json()
    allSnapshots.value = data.snapshots
    snapshotsEnabled.value = data.snapshots_enabled
  } catch (error) {
    loadError.value = error.message
  } finally {
    loading.value = false
  }
}

async function createSnapshot() {
  createError.value = ''
  createLoading.value = true
  try {
    const response = await fetch('/api/volume/snapshots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || response.statusText)
    await loadSnapshots()
  } catch (error) {
    createError.value = error.message
  } finally {
    createLoading.value = false
  }
}

async function deleteSnapshot(row) {
  deletingTag.value = row.tag
  loadError.value = ''
  try {
    const response = await fetch(`/api/volume/snapshots/${row.tag}`, {
      method: 'DELETE',
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || response.statusText)
    await loadSnapshots()
  } catch (error) {
    loadError.value = error.message
  } finally {
    deletingTag.value = ''
  }
}

function openRollbackDialog(row) {
  rollbackRow.value = row
  rollbackError.value = ''
  showRollbackDialog.value = true
}

async function confirmRollback() {
  rollbackLoading.value = true
  rollbackError.value = ''
  try {
    const response = await fetch(
      `/api/volume/snapshots/${rollbackRow.value.tag}/rollback`,
      { method: 'POST' },
    )
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || response.statusText)
    showRollbackDialog.value = false
    rollbackRow.value = null
    await loadSnapshots()
  } catch (error) {
    rollbackError.value = error.message
  } finally {
    rollbackLoading.value = false
  }
}

onMounted(loadSnapshots)
</script>

<template>
  <div class="pt-4">

    <div class="mb-3 flex items-center justify-between gap-3">
      <div class="flex-1 text-sm">
        <ErrorMessage v-if="createError" :message="createError" />
        <span v-else-if="!snapshotsEnabled" class="text-ink-gray-4">
          Snapshots are unavailable — enable a ZFS volume for this bench to create snapshots.
        </span>
        <span v-else class="text-ink-gray-5">
          {{ rows.length }} snapshot{{ rows.length !== 1 ? 's' : '' }}
        </span>
      </div>
      <Button variant="subtle" :loading="createLoading" @click="createSnapshot">
        Create Snapshot
      </Button>
    </div>

    <ErrorMessage v-if="loadError" :message="loadError" />
    <LoadingText v-else-if="loading" />
    <template v-else>
      <!-- Mobile: stacked cards so the row actions stay reachable without horizontal scroll -->
      <div class="flex flex-col gap-2 md:hidden">
        <p v-if="!rows.length" class="py-8 text-center text-sm text-ink-gray-4">No snapshots yet.</p>
        <div
          v-for="row in rows"
          :key="row.tag"
          class="rounded-lg border border-outline-gray-1 px-4 py-3"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="truncate font-medium text-ink-gray-8">{{ row.tag }}</span>
            <span class="shrink-0 text-xs text-ink-gray-4">{{ row.formattedSize }}</span>
          </div>
          <div class="mt-0.5 text-xs text-ink-gray-4">{{ row.formattedDate }}</div>
          <div class="mt-2 flex gap-2">
            <Button variant="ghost" size="sm" @click="openRollbackDialog(row)">Rollback</Button>
            <Button
              variant="ghost"
              theme="red"
              size="sm"
              :loading="deletingTag === row.tag"
              @click="deleteSnapshot(row)"
            >Delete</Button>
          </div>
        </div>
      </div>

      <!-- Desktop: full table -->
      <div class="hidden md:block">
        <ListView
          :columns="columns"
          :rows="rows"
          row-key="tag"
          :options="{ selectable: false, showTooltip: false }"
        >
          <template #cell="{ column, row }">
            <Button
              v-if="column.key === '_rollback'"
              variant="ghost"
              size="sm"
              @click="openRollbackDialog(row)"
            >
              Rollback
            </Button>
            <Button
              v-else-if="column.key === '_delete'"
              variant="ghost"
              theme="red"
              size="sm"
              :loading="deletingTag === row.tag"
              @click="deleteSnapshot(row)"
            >
              Delete
            </Button>
            <span v-else class="block truncate">{{ row[column.key] }}</span>
          </template>
        </ListView>
      </div>
    </template>
  </div>

  <Dialog v-model="showRollbackDialog" :options="{ title: 'Rollback Snapshot', size: 'md' }">
    <template #body-content>
      <div class="space-y-3 text-sm text-ink-gray-7">
        <p>
          Roll back this bench to snapshot
          <code class="rounded bg-surface-gray-2 px-1 py-0.5 text-ink-gray-9">{{ rollbackRow?.tag }}</code>?
        </p>
        <p class="text-ink-red-4">
          All data written after this snapshot was taken — both bench files and the database —
          will be permanently lost. Any snapshots newer than this one will also be destroyed.
        </p>
        <div class="rounded border border-outline-amber-1 bg-surface-amber-1 p-3 text-ink-amber-3">
          <p class="font-medium">MariaDB will be stopped and sites put into maintenance mode</p>
          <p class="mt-1 text-ink-amber-2">The following commands will run in order:</p>
          <pre class="mt-2 rounded bg-surface-white px-3 py-2 font-mono text-xs text-ink-gray-9">sudo systemctl stop mariadb
sudo zfs rollback -r {{ rollbackRow?.tag }}
sudo systemctl start mariadb</pre>
          <p class="mt-2 text-ink-amber-2">
            Ensure no critical database operations are in progress before proceeding.
          </p>
        </div>
        <ErrorMessage v-if="rollbackError" :message="rollbackError" />
        <div class="flex justify-end gap-2 pt-1">
          <Button variant="ghost" @click="showRollbackDialog = false">Cancel</Button>
          <Button variant="solid" theme="red" :loading="rollbackLoading" @click="confirmRollback">
            Rollback
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>
