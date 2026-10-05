<script setup lang="ts">
import { Button, Radio, RadioGroup, TextInput } from 'frappe-ui'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { sitesApi } from '@/api/sites'
import type { FrappeCloudBackup, FrappeCloudConnection } from '@/types/siteBackups'
import { errorMessage } from '@/utils/error'
import { formatBytes } from '@/utils/format'
import { fmtDateTime } from '@/utils/taskFormat'

const POLL_MS = 3_000
const MAX_BACKUP_CHECK_FAILURES = 5
// Frappe Cloud sends backups in pages of this size.
const PAGE_LENGTH = 5

const props = defineProps<{ siteName: string }>()
const backup = defineModel<string>('backup', { default: '' })
const error = defineModel<string>('error', { default: '' })
const isAuthorized = defineModel<boolean>('authorized', { default: false })

const domain = ref('')
const connection = ref<FrappeCloudConnection | null>(null)
const backups = ref<FrappeCloudBackup[] | null>(null)
const connecting = ref(false)
const pendingBackup = ref('')
const hasMoreBackups = ref(false)
const loadingMore = ref(false)
const backupJobUrl = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let backupCheckFailures = 0

const run = async (action: () => Promise<void>, fallback: string) => {
  error.value = ''
  try {
    await action()
  } catch (e) {
    error.value = errorMessage(e, fallback)
  }
}

const loadBackups = async () => {
  const list = await sitesApi.frappeCloud.backups(props.siteName)
  backups.value = list.backups
  hasMoreBackups.value = list.backups.length === PAGE_LENGTH
  backup.value = list.backups[0]?.name ?? ''
  if (list.running_backup && !pendingBackup.value) {
    pendingBackup.value = list.running_backup
    await followBackup()
  }
}

const loadMoreBackups = () => {
  loadingMore.value = true
  return run(async () => {
    const list = await sitesApi.frappeCloud.backups(props.siteName, backups.value?.length ?? 0)
    backups.value = [...(backups.value ?? []), ...list.backups]
    hasMoreBackups.value = list.backups.length === PAGE_LENGTH
  }, 'Could not load more backups.').finally(() => (loadingMore.value = false))
}

const follow = async (current: FrappeCloudConnection) => {
  connection.value = current
  // A model updates only after the parent re-renders, so it is not read back here.
  const isApproved = current.status === 'Approved'
  isAuthorized.value = isApproved
  if (isApproved) await loadBackups()
  if (current.status !== 'Pending') return
  timer = setTimeout(
    () =>
      run(
        async () => follow(await sitesApi.frappeCloud.connection(props.siteName)),
        'Could not check the request.',
      ),
    POLL_MS,
  )
}

const connect = () => {
  connecting.value = true
  return run(async () => {
    await follow(await sitesApi.frappeCloud.connect(props.siteName, domain.value.trim()))
  }, 'Could not reach Frappe Cloud.').finally(() => (connecting.value = false))
}

const checkBackupLater = () => {
  timer = setTimeout(() => run(followBackup, 'Could not check the backup.'), POLL_MS)
}

const stopFollowingBackup = () => {
  pendingBackup.value = ''
  backupJobUrl.value = ''
  backupCheckFailures = 0
}

const followBackup = async () => {
  let result: { status: string; job_url: string | null }
  try {
    result = await sitesApi.frappeCloud.backup(props.siteName, pendingBackup.value)
  } catch (failure) {
    // A short outage keeps the backup followed; a lasting one frees the button.
    if (++backupCheckFailures > MAX_BACKUP_CHECK_FAILURES) stopFollowingBackup()
    else checkBackupLater()
    throw failure
  }
  backupCheckFailures = 0
  backupJobUrl.value = result.job_url ?? ''
  if (result.status === 'Pending' || result.status === 'Running') return checkBackupLater()
  stopFollowingBackup()
  if (result.status !== 'Success') throw new Error('Frappe Cloud could not take the backup.')
  await loadBackups()
}

const takeBackup = () =>
  run(async () => {
    pendingBackup.value = (await sitesApi.frappeCloud.takeBackup(props.siteName)).name
    await followBackup()
  }, 'Could not take a backup.')

const openApproval = () => window.open(connection.value?.approval_url, '_blank', 'noopener')
const openBackupJob = () => window.open(backupJobUrl.value, '_blank', 'noopener')

const reset = () => {
  clearTimeout(timer)
  connection.value = null
  isAuthorized.value = false
  backups.value = null
  backup.value = ''
}

const cancel = () =>
  run(async () => {
    await sitesApi.frappeCloud.disconnect(props.siteName)
    reset()
  }, 'Could not cancel the request.')

onMounted(async () => {
  try {
    await follow(await sitesApi.frappeCloud.connection(props.siteName))
  } catch {
    // No connection yet: the form is shown.
  }
})
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div v-if="!connection || !['Pending', 'Approved'].includes(connection.status)" class="space-y-3">
    <p v-if="connection" class="text-p-sm text-ink-gray-6">
      The request for {{ connection.remote_site }} is {{ connection.status.toLowerCase() }}. Connect
      again.
    </p>
    <TextInput v-model="domain" label="Site on Frappe Cloud" placeholder="erp.example.com" />
    <Button :loading="connecting" :disabled="!domain.trim()" @click="connect">Connect</Button>
  </div>

  <div v-else-if="connection.status === 'Pending'" class="space-y-3">
    <p class="text-p-sm text-ink-gray-7">
      Open Frappe Cloud and enter this pass code to allow access to the backups of
      <b>{{ connection.remote_site }}</b>.
    </p>
    <p class="font-mono text-2xl tracking-[0.3em] text-ink-gray-9 select-all">
      {{ connection.code }}
    </p>
    <div class="flex items-center gap-2">
      <Button variant="solid" icon-right="lucide-external-link" @click="openApproval">
        Open Frappe Cloud
      </Button>
      <Button variant="subtle" @click="cancel">Cancel</Button>
    </div>
    <p class="text-p-sm text-ink-gray-5">Waiting for approval…</p>
  </div>

  <div v-else class="space-y-3">
    <div class="flex items-center justify-between">
      <p class="text-p-sm text-ink-gray-7">Backups of <b>{{ connection.remote_site }}</b></p>
      <Button size="sm" variant="ghost" @click="cancel">Change site</Button>
    </div>
    <RadioGroup
      v-model="backup"
      class="rounded-6 border border-outline-gray-1 [&_[role=radiogroup]]:gap-0"
    >
      <div
        v-for="item in backups ?? []"
        :key="item.name"
        class="flex items-center gap-3 border-t border-outline-gray-1 px-3 py-2.5 first:border-t-0"
      >
        <Radio :value="item.name" :label="fmtDateTime(item.created_at)" class="flex-1" />
        <span class="text-sm text-ink-gray-5">{{ formatBytes(item.size_bytes) }}</span>
      </div>
      <p v-if="backups && !backups.length" class="px-3 py-2.5 text-p-sm text-ink-gray-5">
        No backups yet. Take a new backup.
      </p>
    </RadioGroup>
    <div class="flex items-center gap-2">
      <Button :loading="Boolean(pendingBackup)" @click="takeBackup">
        {{ pendingBackup ? 'Backing up' : 'Take new backup' }}
      </Button>
      <Button
        v-if="backupJobUrl"
        variant="ghost"
        icon-right="lucide-external-link"
        @click="openBackupJob"
      >
        View job
      </Button>
      <Button
        v-if="hasMoreBackups"
        class="ml-auto"
        variant="subtle"
        :loading="loadingMore"
        @click="loadMoreBackups"
      >
        Load more
      </Button>
    </div>
  </div>
</template>
