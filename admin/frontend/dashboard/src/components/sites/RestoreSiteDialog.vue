<script setup lang="ts">
import { Button, Checkbox, Select, TabButtons, TextInput } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import ActionDialog from '@/components/common/ActionDialog.vue'
import FrappeCloudSource from '@/components/sites/FrappeCloudSource.vue'
import { useBackupUpload } from '@/composables/sites/useBackupUpload'
import { useSites } from '@/composables/sites/useSites'
import type { RemoteBackup } from '@/types/siteBackups'
import type { TaskPayload } from '@/types/tasks'
import { errorMessage } from '@/utils/error'
import { fmtDateTime } from '@/utils/taskFormat'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  siteName: string
}

const props = defineProps<Props>()
const open = defineModel<boolean>('open')

const router = useRouter()
const route = useRoute()
const { names: siteNames, load: loadSites } = useSites()

type Source = 'upload' | 'site' | 'remote' | 'frappe-cloud'

const SOURCES = [
  { value: 'upload', label: 'Upload files' },
  { value: 'site', label: 'Other site' },
  { value: 'remote', label: 'Remote site' },
  { value: 'frappe-cloud', label: 'Frappe Cloud v1' },
]
const PARTS = [
  { part: 'database', label: 'Database', hint: 'Usually ends in .sql.gz or .sql', accept: '.sql,.gz' },
  { part: 'public', label: 'Public files', hint: 'Usually ends in -files.tar', accept: '.tar,.tgz' },
  {
    part: 'private',
    label: 'Private files',
    hint: 'Usually ends in -private-files.tar',
    accept: '.tar,.tgz',
  },
  {
    part: 'config',
    label: 'Site config',
    hint: 'Usually ends in -site_config_backup.json',
    accept: '.json',
  },
]

const source = ref<Source>('upload')
const sourceSite = ref('')
const remoteSite = ref('')
const password = ref('')
const remoteBackups = ref<RemoteBackup[] | null>(null)
const remoteBackup = ref('')
const frappeCloudBackup = ref('')
const isFrappeCloudAuthorized = ref(false)
const fetchingBackups = ref(false)
const chosen = ref<Record<string, boolean>>({})
const skipFailingPatches = ref(false)
const uploads = ref<Record<string, File | null>>({})
const inputs: Record<string, HTMLInputElement | null> = {}
const restoring = ref(false)
const backupUpload = useBackupUpload(() => props.siteName)
const error = ref('')

const sourceOptions = computed(() =>
  siteNames.value.filter((name) => name !== props.siteName).map((name) => ({ label: name, value: name })),
)
const remoteBackupOptions = computed(() =>
  (remoteBackups.value ?? []).map((backup) => ({
    label: fmtDateTime(backup.created_at),
    value: backup.timestamp,
  })),
)
const selectedRemoteBackup = computed(() =>
  remoteBackups.value?.find(({ timestamp }) => timestamp === remoteBackup.value),
)
const availableParts = computed(() =>
  source.value === 'remote'
    ? (selectedRemoteBackup.value?.parts ?? [])
    : PARTS.map(({ part }) => part),
)
const parts = computed(() =>
  PARTS.map(({ part }) => part).filter((part) =>
    source.value === 'upload'
      ? uploads.value[part]
      : chosen.value[part] && availableParts.value.includes(part),
  ),
)
// Remote and Frappe Cloud sources show the restore options once a backup is chosen.
const hasChosenBackup = computed(() => {
  if (source.value === 'remote') return Boolean(selectedRemoteBackup.value)
  if (source.value === 'frappe-cloud') return isFrappeCloudAuthorized.value
  return true
})
const isReady = computed(() => {
  if (!parts.value.length) return false
  if (source.value === 'site') return Boolean(sourceSite.value)
  if (source.value === 'remote') return Boolean(selectedRemoteBackup.value)
  if (source.value === 'frappe-cloud') return Boolean(frappeCloudBackup.value)
  return true
})

const sourceInUrl = (): Source => {
  const value = route.query.restore
  return SOURCES.some((option) => option.value === value) ? (value as Source) : 'upload'
}

const setRestoreQuery = (value?: Source) => {
  const { restore: _, ...query } = route.query
  router.replace({ query: value ? { ...query, restore: value } : query })
}

watch(source, (value) => open.value && setRestoreQuery(value))

watch(
  open,
  (isOpen) => {
    password.value = ''
    // Uploaded parts stay for a retry until the dialog closes.
    if (!isOpen) backupUpload.cancel()
    if (!isOpen) {
      if (route.query.restore) setRestoreQuery()
      return
    }
    source.value = sourceInUrl()
    setRestoreQuery(source.value)
    sourceSite.value = ''
    remoteSite.value = ''
    chosen.value = { database: true, public: true, private: true, config: true }
    skipFailingPatches.value = false
    uploads.value = {}
    error.value = ''
    loadSites()
  },
  { immediate: true },
)

watch([remoteSite, password], () => {
  remoteBackups.value = null
  remoteBackup.value = ''
  error.value = ''
})

const getBackups = async () => {
  fetchingBackups.value = true
  error.value = ''
  try {
    const data = await sitesApi.remoteBackups(props.siteName, remoteSite.value.trim(), password.value)
    if (!('backups' in data))
      error.value = apiErrorMessage(data, 'Could not get the backups of this site.')
    else if (!data.backups.length) error.value = 'No backups found. Take a backup on that site first.'
    else {
      remoteBackups.value = data.backups
      remoteBackup.value = data.backups[0].timestamp
    }
  } catch (e) {
    error.value = errorMessage(e, 'Could not get the backups of this site.')
  } finally {
    fetchingBackups.value = false
  }
}

const pickFile = (part: string, event: Event) => {
  const input = event.target as HTMLInputElement
  uploads.value[part] = input.files?.[0] ?? null
  input.value = ''
}

const removeFile = (part: string) => {
  uploads.value[part] = null
}

const submit = (): Promise<TaskPayload> => {
  if (source.value === 'upload') return restoreUploadedFiles()
  if (source.value === 'frappe-cloud')
    return sitesApi.restore(props.siteName, {
      parts: parts.value,
      skip_failing_patches: skipFailingPatches.value,
      frappe_cloud_backup: frappeCloudBackup.value,
    })
  if (source.value === 'remote')
    return sitesApi.restore(props.siteName, {
      parts: parts.value,
      skip_failing_patches: skipFailingPatches.value,
      remote_site: remoteSite.value.trim(),
      password: password.value,
      backup_timestamp: remoteBackup.value,
    })
  return sitesApi.restore(props.siteName, {
    parts: parts.value,
    skip_failing_patches: skipFailingPatches.value,
    source_site: sourceSite.value,
  })
}

const restoreUploadedFiles = async (): Promise<TaskPayload> => {
  const files = Object.fromEntries(parts.value.map((part) => [part, uploads.value[part] as File]))
  return sitesApi.restore(props.siteName, {
    parts: parts.value,
    skip_failing_patches: skipFailingPatches.value,
    upload_id: await backupUpload.upload(files),
  })
}

const uploadHint = (part: string, hint: string) => {
  const file = uploads.value[part]
  if (!file) return hint
  const sent = backupUpload.progress.value[part]
  if (sent === undefined) return file.name
  return sent === 1 ? `${file.name} · Uploaded` : `${file.name} · ${Math.floor(sent * 100)}%`
}

const restore = async () => {
  restoring.value = true
  error.value = ''
  try {
    const data = await submit()
    if (data.task_id) {
      open.value = false
      openTaskDetailPage(router, data.task_id)
    } else error.value = apiErrorMessage(data, 'Could not start the restore.')
  } catch (e) {
    error.value = errorMessage(e, 'Could not start the restore.')
  } finally {
    restoring.value = false
  }
}
</script>

<template>
  <ActionDialog
    v-model:open="open"
    title="Restore Site"
    size="lg"
    :error="error"
    confirm-label="Restore"
    confirm-theme="red"
    :loading="restoring"
    :disabled="!isReady"
    :hide-actions="!hasChosenBackup"
    @confirm="restore"
  >
    <div class="space-y-4">
      <TabButtons v-model="source" :options="SOURCES" class="w-full" />

      <div v-if="source === 'upload'" class="divide-y divide-outline-gray-1">
        <div
          v-for="item in PARTS"
          :key="item.part"
          class="flex justify-between items-center gap-4 py-2.5"
        >
          <div class="min-w-0">
            <p class="font-medium text-ink-gray-8 text-sm">{{ item.label }}</p>
            <p class="text-ink-gray-5 text-p-sm truncate">
              {{ uploadHint(item.part, item.hint) }}
            </p>
          </div>
          <input
            :ref="(el) => (inputs[item.part] = el as HTMLInputElement | null)"
            type="file"
            :accept="item.accept"
            class="hidden"
            @change="pickFile(item.part, $event)"
          />
          <Button
            v-if="uploads[item.part]"
            variant="ghost"
            icon="lucide-x"
            :aria-label="`Remove ${item.label}`"
            @click="removeFile(item.part)"
          />
          <Button v-else class="shrink-0" @click="inputs[item.part]?.click()">Choose</Button>
        </div>
      </div>

      <template v-else>
        <Select
          v-if="source === 'site'"
          v-model="sourceSite"
          label="Site"
          :options="sourceOptions"
          placeholder="Choose a site"
        />

        <FrappeCloudSource
          v-else-if="source === 'frappe-cloud'"
          v-model:backup="frappeCloudBackup"
          v-model:error="error"
          v-model:authorized="isFrappeCloudAuthorized"
          :site-name="siteName"
        />

        <div v-else class="space-y-3">
          <TextInput v-model="remoteSite" label="Site" placeholder="erp.example.com" />
          <TextInput
            v-model="password"
            label="Administrator password"
            type="password"
            autocomplete="new-password"
            data-1p-ignore
            data-lpignore="true"
            data-bwignore
          />
          <Select
            v-if="remoteBackups"
            v-model="remoteBackup"
            label="Backup"
            :options="remoteBackupOptions"
          />
          <Button
            v-else
            :loading="fetchingBackups"
            :disabled="!remoteSite.trim() || !password"
            @click="getBackups"
          >
            Get backups
          </Button>
        </div>

        <div
          v-if="
            (source !== 'remote' || selectedRemoteBackup) &&
            (source !== 'frappe-cloud' || frappeCloudBackup)
          "
          class="flex flex-wrap gap-1"
        >
          <Checkbox
            v-for="item in PARTS"
            :key="item.part"
            v-model="chosen[item.part]"
            :label="item.label"
            :disabled="!availableParts.includes(item.part)"
            padded
          />
        </div>
      </template>

      <Checkbox
        v-if="hasChosenBackup && (source !== 'frappe-cloud' || frappeCloudBackup)"
        v-model="skipFailingPatches"
        label="Skip failing patches"
        padded
      />
    </div>
  </ActionDialog>
</template>
