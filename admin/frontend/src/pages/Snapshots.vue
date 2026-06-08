<script setup>
import { computed, onMounted, ref } from 'vue'
import { Button, Dialog, ErrorMessage, ListView, LoadingText, Tabs } from 'frappe-ui'

const tabs = [
  { label: 'Benches', dataset: 'benches' },
  { label: 'MariaDB', dataset: 'mariadb' },
]

const activeTab = ref(0)
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

const currentDataset = computed(() => tabs[activeTab.value].dataset)
const isMariadbTab = computed(() => currentDataset.value === 'mariadb')

const columns = [
  { label: 'Snapshot Tag', key: 'tag' },
  { label: 'Created', key: 'formattedDate', width: '180px' },
  { label: 'Used', key: 'formattedSize', width: '100px' },
  { label: '', key: '_rollback', width: '90px' },
  { label: '', key: '_delete', width: '80px' },
]

const rows = computed(() =>
  allSnapshots.value
    .filter(snapshot => snapshot.dataset.endsWith(currentDataset.value))
    .map(snapshot => ({
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
      body: JSON.stringify({ dataset: currentDataset.value }),
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
    const response = await fetch(`/api/volume/snapshots/${currentDataset.value}/${row.tag}`, {
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
      `/api/volume/snapshots/${currentDataset.value}/${rollbackRow.value.tag}/rollback`,
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
  <Tabs :tabs="tabs" v-model="activeTab" @update:modelValue="loadSnapshots">
    <template #tab-panel>
      <div class="pt-4">

        <div class="mb-3 flex items-center justify-between gap-3">
          <div class="flex-1 text-sm">
            <ErrorMessage v-if="createError" :message="createError" />
            <span v-else-if="!snapshotsEnabled" class="text-ink-gray-4">
              Snapshots are disabled — set <code>volume.snapshots.enabled = true</code> in bench.toml to create snapshots.
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
        <ListView
          v-else
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
  </Tabs>

  <Dialog v-model="showRollbackDialog" :options="{ title: 'Rollback Snapshot', size: 'md' }">
    <template #body-content>
      <div class="space-y-3 text-sm text-ink-gray-7">
        <p>
          Roll back <strong>{{ currentDataset }}</strong> to snapshot
          <code class="rounded bg-surface-gray-2 px-1 py-0.5 text-ink-gray-9">{{ rollbackRow?.tag }}</code>?
        </p>
        <p class="text-red-600">
          All data written after this snapshot was taken will be permanently lost.
          Any snapshots newer than this one will also be destroyed.
        </p>
        <div
          v-if="isMariadbTab"
          class="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900"
        >
          <p class="font-medium">MariaDB will be stopped and restarted</p>
          <p class="mt-1 text-amber-700">The following commands will run in order:</p>
          <pre class="mt-2 rounded bg-white px-3 py-2 font-mono text-xs text-ink-gray-9">sudo systemctl stop mariadb
sudo zfs rollback -r {{ rollbackRow?.tag }}
sudo systemctl start mariadb</pre>
          <p class="mt-2 text-amber-700">
            Ensure no critical database operations are in progress before proceeding.
          </p>
        </div>
        <div
          v-else
          class="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900"
        >
          <p class="font-medium">Sites will be put into maintenance mode</p>
          <p class="mt-1 text-amber-700">
            All sites on this bench will be unavailable during the restore. They will be brought
            back online automatically once the rollback completes.
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
