<script setup lang="ts">
import { Button, Checkbox, TextInput } from 'frappe-ui'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import ActionDialog from '@/components/common/ActionDialog.vue'
import RestoreSiteDialog from '@/components/sites/RestoreSiteDialog.vue'
import { errorMessage } from '@/utils/error'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  siteName: string
}

const props = defineProps<Props>()

const siteSubject = computed(() => ({
  label: props.siteName,
  description: 'Site, database and uploaded files',
  icon: 'lucide-globe',
}))

const router = useRouter()
const route = useRoute()

const showMigrate = ref(false)
const migrating = ref(false)
const migrateError = ref('')

const confirmMigrate = async () => {
  migrating.value = true
  migrateError.value = ''
  try {
    const data = await sitesApi.migrate(props.siteName)
    if (data.operation_id) {
      showMigrate.value = false
      router.push({ name: 'UpdateDetail', params: { operationId: data.operation_id } })
    } else migrateError.value = apiErrorMessage(data, 'Failed to migrate site.')
  } catch (e) {
    migrateError.value = errorMessage(e, 'Failed to migrate site.')
  } finally {
    migrating.value = false
  }
}

const DangerActions = [
  {
    key: 'rename',
    label: 'Rename site',
    buttonLabel: 'Rename',
    description: 'Give this site a new name.',
    action: () => {
      newName.value = ''
      keepOldHostname.value = true
      renameError.value = ''
      showRename.value = true
    },
  },
  {
    key: 'migrate',
    label: 'Migrate site',
    buttonLabel: 'Migrate',
    description: 'Run pending database migrations on this site.',
    action: () => {
      migrateError.value = ''
      showMigrate.value = true
    },
  },
  {
    key: 'restore',
    label: 'Restore site',
    buttonLabel: 'Restore',
    description: 'Replace its data with uploaded backup files, another site, or a remote site.',
    action: () => {
      showRestore.value = true
    },
  },
  {
    key: 'reset',
    label: 'Reset site',
    buttonLabel: 'Reset',
    description: 'Wipes the database back to a fresh install. Apps stay; all your data is removed.',
    action: () => {
      confirmName.value = ''
      resetError.value = ''
      showReset.value = true
    },
  },
  {
    key: 'drop',
    label: 'Drop site',
    buttonLabel: 'Drop',
    description: `Permanently deletes ${props.siteName} and all its data.`,
    action: () => {
      confirmName.value = ''
      dropError.value = ''
      takeBackup.value = true
      showDrop.value = true
    },
  },
]

// The Restore dialog keeps its tab in ?restore=, so a reload opens it again.
const showRestore = ref(Boolean(route.query.restore))

const showRename = ref(false)
const renaming = ref(false)
const renameError = ref('')
const newName = ref('')
const keepOldHostname = ref(true)

const confirmRename = async () => {
  renaming.value = true
  renameError.value = ''
  try {
    const data = await sitesApi.rename(props.siteName, newName.value.trim(), keepOldHostname.value)
    if (data.task_id) {
      showRename.value = false
      openTaskDetailPage(router, data.task_id)
    } else renameError.value = apiErrorMessage(data, 'Failed to rename site.')
  } catch (e) {
    renameError.value = errorMessage(e, 'Failed to rename site.')
  } finally {
    renaming.value = false
  }
}

const confirmName = ref('')

const showReset = ref(false)
const resetting = ref(false)
const resetError = ref('')

const confirmReset = async () => {
  resetting.value = true
  resetError.value = ''
  try {
    const data = await sitesApi.reinstall(props.siteName)
    if (data.task_id) {
      showReset.value = false
      openTaskDetailPage(router, data.task_id)
    } else resetError.value = apiErrorMessage(data, 'Failed to reset site.')
  } catch (e) {
    resetError.value = errorMessage(e, 'Failed to reset site.')
  } finally {
    resetting.value = false
  }
}

const showDrop = ref(false)
const dropping = ref(false)
const dropError = ref('')
const takeBackup = ref(true)

const confirmDrop = async () => {
  dropping.value = true
  dropError.value = ''
  try {
    const data = await sitesApi.drop(props.siteName, { noBackup: !takeBackup.value })
    if (data.task_id) {
      showDrop.value = false
      openTaskDetailPage(router, data.task_id)
    } else {
      dropError.value = apiErrorMessage(data, 'Failed to drop site.')
      dropping.value = false
    }
  } catch (e) {
    dropError.value = errorMessage(e, 'Failed to drop site.')
    dropping.value = false
  }
}
</script>

<template>
  <h2 class="mb-3 mt-3 text-base-semibold text-ink-gray-8">Danger</h2>

  <div
    v-for="d in DangerActions"
    :key="d.key"
    class="flex justify-between items-start gap-x-2.5 py-4 border-b last:border-b-0 border-outline-alpha-gray-1"
  >
    <div class="flex flex-col gap-1 min-w-0">
      <p class="font-medium text-ink-gray-8">{{ d.label }}</p>
      <p class="text-ink-gray-6 text-p-sm line-clamp-2 sm:line-clamp-none">
        {{ d.description }}
      </p>
    </div>

    <Button theme="red" class="ml-4 shrink-0" @click="d.action"
      >{{ d.buttonLabel || d.label }}</Button
    >
  </div>

  <ActionDialog
    v-model:open="showMigrate"
    title="Migrate Site"
    :error="migrateError"
    confirm-label="Migrate"
    confirm-theme="red"
    :loading="migrating"
    @confirm="confirmMigrate"
  >
    <p class="text-ink-gray-6 text-p-sm">The site might go down while this runs.</p>
  </ActionDialog>

  <RestoreSiteDialog v-model:open="showRestore" :site-name="siteName" />

  <ActionDialog
    v-model:open="showRename"
    title="Rename Site"
    :error="renameError"
    confirm-label="Rename"
    :loading="renaming"
    :disabled="!newName.trim() || newName.trim() === siteName"
    @confirm="confirmRename"
  >
    <p class="text-ink-gray-6 text-p-sm">The site will be offline for a moment while it is renamed.</p>
    <div>
      <TextInput v-model="newName" placeholder="prod.example.com" class="w-full">
        <template #label>
          <span class="text-sm">New name</span>
        </template>
      </TextInput>
      <Checkbox
        v-model="keepOldHostname"
        class="mt-3"
        :label="`Keep ${siteName} working as well`"
      />
    </div>
  </ActionDialog>

  <ActionDialog
    v-model:open="showReset"
    title="Reset Site"
    :subject="siteSubject"
    :warning="{
      title: `This can't be undone.`,
      message: `Every record on ${siteName} is wiped and the database goes back to a fresh install. Installed apps stay.`,
    }"
    :error="resetError"
    confirm-label="Reset site"
    confirm-theme="red"
    :loading="resetting"
    :disabled="confirmName !== siteName"
    @confirm="confirmReset"
  >
    <template #after-warning>
      <TextInput v-model="confirmName" :placeholder="siteName" class="w-full">
        <template #label>
          <span class="text-sm">Type the site name to confirm</span>
        </template>
      </TextInput>
    </template>
  </ActionDialog>

  <ActionDialog
    v-model:open="showDrop"
    title="Drop Site"
    :subject="siteSubject"
    :warning="{
      title: `This can't be undone.`,
      message: `The database and every file belonging to ${siteName} will be deleted. Existing backups are kept for 30 days.`,
    }"
    :error="dropError"
    confirm-label="Drop site"
    confirm-theme="red"
    :loading="dropping"
    :disabled="confirmName !== siteName"
    @confirm="confirmDrop"
  >
    <template #after-warning>
      <TextInput v-model="confirmName" :placeholder="siteName" class="w-full">
        <template #label>
          <span class="text-sm">Type the site name to confirm</span>
        </template>
      </TextInput>
      <Checkbox v-model="takeBackup" label="Take a backup before dropping" />
    </template>
  </ActionDialog>
</template>
