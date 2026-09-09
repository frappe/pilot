<script setup lang="ts">
import { Alert, Button, Dialog, ErrorMessage, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'

import { apiErrorMessage } from '@/api/client'
import { databaseApi } from '@/api/database'
import { formatBytes } from '@/utils/format'

interface BinlogFile {
  name: string
  size_bytes: number
}

interface Props {
  bytes: number
}

const props = defineProps<Props>()
const emit = defineEmits<{ purged: [] }>()

const showThresholdBytes = 1024 ** 3
const minFilesToShow = 2

const dialogOpen = ref(false)
const loading = ref(false)
const purging = ref(false)
const loadError = ref('')
const purgeError = ref('')
const binlogs = ref<BinlogFile[]>([])
const binlogsLoaded = ref(false)

const show = computed(
  () =>
    props.bytes > showThresholdBytes &&
    binlogsLoaded.value &&
    binlogs.value.length > minFilesToShow,
)

// Files come oldest-first; the last one is the active file PURGE always keeps.
const keepFile = computed(() => binlogs.value.at(-1) ?? null)
const freedBytes = computed(() =>
  binlogs.value.slice(0, -1).reduce((total, file) => total + file.size_bytes, 0),
)
const canPurge = computed(() => binlogs.value.length > 1)

const loadBinlogs = async () => {
  loadError.value = ''
  loading.value = true

  try {
    const result = await databaseApi.binlogs.list()
    if (result?.error) throw new Error(apiErrorMessage(result, 'Could not load binary logs.'))

    binlogs.value = Array.isArray(result) ? result : []
    binlogsLoaded.value = true
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : 'Could not load binary logs.'
  } finally {
    loading.value = false
  }
}

const openDialog = () => {
  dialogOpen.value = true
  purgeError.value = ''
  loadBinlogs()
}

onMounted(loadBinlogs)

const purge = async () => {
  if (!keepFile.value) return

  purging.value = true
  purgeError.value = ''

  try {
    const result = await databaseApi.binlogs.purge(keepFile.value.name)
    if (result?.error) throw new Error(apiErrorMessage(result, 'Could not purge binary logs.'))

    dialogOpen.value = false
    toast.success('Binary logs purged')
    emit('purged')
  } catch (e) {
    purgeError.value = e instanceof Error ? e.message : 'Could not purge binary logs.'
  } finally {
    purging.value = false
  }
}
</script>

<template>
  <Alert
    v-if="show"
    title="Binary logs are taking up space"
    :dismissible="false"
    class="mt-4 !bg-surface-blue-1 border border-outline-gray-2"
  >
    <template #description>
      <p class="text-ink-gray-6 prose-sm">
        Binary logs are using {{ formatBytes(bytes) }}. Purge older logs to free up space.
      </p>
    </template>

    <template #footer>
      <Button theme="blue" class="col-span-2 ml-auto" @click="openDialog"
        >Purge binary logs</Button
      >
    </template>
  </Alert>

  <Dialog v-model="dialogOpen" title="Purge binary logs?" size="sm">
    <p v-if="loading" class="text-ink-gray-6 text-sm">Loading binary logs…</p>

    <template v-else-if="!loadError">
      <p v-if="!canPurge" class="text-ink-gray-7 text-p-sm">
        There's only one binary log file right now, so there's nothing to purge yet.
      </p>

      <p v-else class="text-ink-gray-7 text-p-sm">
        All binary logs except the most recent are deleted, freeing about
        {{ formatBytes(freedBytes) }}. The most recent log is kept so replication and
        point-in-time recovery keep working.
      </p>
    </template>

    <ErrorMessage v-if="loadError" :message="loadError" />
    <ErrorMessage v-if="purgeError" :message="purgeError" class="mt-3" />

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="dialogOpen = false">Cancel</Button>
        <Button
          v-if="!loading && !loadError"
          variant="solid"
          :loading="purging"
          :disabled="!canPurge"
          @click="purge"
        >
          Purge
        </Button>
      </div>
    </template>
  </Dialog>
</template>
