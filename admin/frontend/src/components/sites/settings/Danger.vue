<template>
  <div>
    <p class="font-semibold text-ink-gray-8 text-base">Danger</p>
    <div class="mt-1">
      <div v-for="d in DangerActions" :key="d.key"
        class="flex justify-between items-start gap-x-2.5 py-4 border-b last:border-b-0 border-outline-alpha-gray-1">
        <div class="flex flex-col min-w-0">
          <p class="font-medium text-ink-gray-8 text-sm leading-normal">{{ d.label }}</p>
          <div class="mt-0.5">
            <p class="text-ink-gray-6 text-sm line-clamp-2 sm:line-clamp-none">{{ d.description }}</p>
          </div>
        </div>
        <Button size="sm" theme="red" class="ml-4 shrink-0" @click="d.action">{{ d.buttonLabel || d.label }}</Button>
      </div>
    </div>
  </div>

  <!-- Migrate dialog -->
  <Dialog v-model="showMigrate" :options="{ title: 'Migrate this site', size: 'md' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        This runs <span class="font-mono text-ink-gray-8">bench migrate</span> on
        <span class="font-semibold text-ink-gray-8 break-all">{{ siteName }}</span> without taking a backup first.
        If the migration fails partway, you'll need an existing backup to recover.
      </p>
      <ErrorMessage v-if="migrateError" :message="migrateError" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="outline" @click="showMigrate = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="migrating" @click="confirmMigrate">Migrate</Button>
      </div>
    </template>
  </Dialog>

  <!-- Reset dialog -->
  <Dialog v-model="showReset" :options="{ title: 'Reset this site', size: 'md' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        This reinstalls <span class="font-semibold text-ink-gray-8 break-all">{{ siteName }}</span> and wipes
        all its data. Apps stay installed.
      </p>
      <TextInput v-model="confirmName" :placeholder="siteName" class="mt-4 w-full">
        <template #label>
          <span class="text-sm break-all">Type {{ siteName }} to confirm</span>
        </template>
      </TextInput>
      <ErrorMessage v-if="resetError" :message="resetError" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="outline" @click="showReset = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="resetting" :disabled="confirmName !== siteName"
          @click="confirmReset">
          Reset site
        </Button>
      </div>
    </template>
  </Dialog>

  <!-- Drop dialog -->
  <Dialog v-model="showDrop" :options="{ title: 'Delete this site', size: 'md' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-p-sm">
        This permanently deletes <span class="font-semibold text-ink-gray-8 break-all">{{ siteName }}</span>
        and everything on it. Backups are kept for 30 days.
      </p>
      <TextInput v-model="confirmName" :placeholder="siteName" class="mt-4 w-full">
        <template #label>
          <span class="text-sm break-all">Type {{ siteName }} to confirm</span>
        </template>
      </TextInput>
      <ErrorMessage v-if="dropError" :message="dropError" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="outline" @click="showDrop = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="dropping" :disabled="confirmName !== siteName"
          @click="confirmDrop">
          Delete site
        </Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, ErrorMessage, TextInput } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { openTaskDetailPage } from '@/utils/taskRoute'

const props = defineProps({ siteName: { type: String, required: true } })

const router = useRouter()

const showMigrate = ref(false)
const migrating = ref(false)
const migrateError = ref('')

async function confirmMigrate() {
  migrating.value = true
  migrateError.value = ''
  try {
    const data = await sitesApi.migrate(props.siteName)
    if (data.task_id) {
      showMigrate.value = false
      openTaskDetailPage(router, data.task_id)
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
    description: 'Runs bench migrate for this site without taking a backup first.',
    action: () => { migrateError.value = ''; showMigrate.value = true },
  },
  {
    key: 'reset',
    label: 'Reset site',
    description: 'Wipes the database back to a fresh install. Apps stay; all your data is removed.',
    action: () => { confirmName.value = ''; resetError.value = ''; showReset.value = true },
  },
  {
    key: 'drop',
    label: 'Drop site',
    description: `Permanently deletes ${props.siteName} and all its data.`,
    action: () => { confirmName.value = ''; dropError.value = ''; showDrop.value = true },
  },
]

const confirmName = ref('')

const showReset = ref(false)
const resetting = ref(false)
const resetError = ref('')

async function confirmReset() {
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

async function confirmDrop() {
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
