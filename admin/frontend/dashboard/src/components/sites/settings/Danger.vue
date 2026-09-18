<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, TextInput } from 'frappe-ui'

import ActionDialog from '@/components/common/ActionDialog.vue'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
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
    migrateError.value = e.message || 'Failed to migrate site.'
  } finally {
    migrating.value = false
  }
}

const DangerActions = [
  {
    key: 'migrate',
    label: 'Migrate site',
    buttonLabel: 'Migrate',
    description: 'Creates a recovery backup, then migrates this site.',
    action: () => {
      migrateError.value = ''
      showMigrate.value = true
    },
  },
  {
    key: 'reset',
    label: 'Reset site',
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
    description: `Permanently deletes ${props.siteName} and all its data.`,
    action: () => {
      confirmName.value = ''
      dropError.value = ''
      showDrop.value = true
    },
  },
]

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
    resetError.value = e.message || 'Failed to reset site.'
  } finally {
    resetting.value = false
  }
}

const showDrop = ref(false)
const dropping = ref(false)
const dropError = ref('')

const confirmDrop = async () => {
  dropping.value = true
  dropError.value = ''
  try {
    const data = await sitesApi.drop(props.siteName)
    if (data.task_id) {
      showDrop.value = false
      openTaskDetailPage(router, data.task_id)
    } else {
      dropError.value = apiErrorMessage(data, 'Failed to drop site.')
      dropping.value = false
    }
  } catch (e) {
    dropError.value = e.message || 'Failed to drop site.'
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
    :subject="siteSubject"
    :warning="{
      title: 'The site goes down while this runs.',
      message: `A recovery backup is taken first. If the migration fails you can retry it, or restore that backup from the update page.`,
    }"
    :error="migrateError"
    confirm-label="Migrate"
    confirm-theme="red"
    :loading="migrating"
    @confirm="confirmMigrate"
  />

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
          <span class="text-sm break-all">Type {{ siteName }} to confirm</span>
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
      message: `The database and every file belonging to ${siteName} are deleted. Existing backups are kept for 30 days.`,
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
          <span class="text-sm break-all">Type {{ siteName }} to confirm</span>
        </template>
      </TextInput>
    </template>
  </ActionDialog>
</template>
