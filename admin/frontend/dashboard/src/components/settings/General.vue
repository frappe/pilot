<script setup lang="ts">
import { Button, Dialog, ErrorMessage, Spinner, Switch, toast } from 'frappe-ui'
import { onMounted, ref } from 'vue'
import { benchesApi } from '@/api/benches'
import { apiErrorMessage, hasApiError } from '@/api/client'
import { settingsApi } from '@/api/settings'
import SettingsRow from '@/components/settings/SettingsRow.vue'
import CommonSiteConfig from '@/components/settings/CommonSiteConfig.vue'
import WebWorkers from '@/components/settings/WebWorkers.vue'
import Workers from '@/components/settings/Workers.vue'
import { useSession } from '@/composables/auth/useSession'
import type { GunicornSettings } from '@/types/settings'
import { errorMessage } from '@/utils/error'

const { session } = useSession()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const codeEditor = ref(true)
const liteMode = ref(false)
const liteModeSupported = ref(false)
const hasBucket = ref(false)
const bucketBackups = ref(true)
const production = ref(false)
const gunicorn = ref<GunicornSettings>()
const restartOpen = ref(false)
const restarting = ref(false)
const restartError = ref('')

const toggleCodeEditor = async (value: boolean) => {
  saving.value = true
  error.value = ''
  try {
    await settingsApi.update({ bench: { enable_code_editor: value } })
    codeEditor.value = value
    session.codeEditor = value
    toast.success(`Code editor ${value ? 'turned on' : 'turned off'}`)
  } catch (e) {
    error.value = errorMessage(e, 'Could not update the code editor setting.')
  } finally {
    saving.value = false
  }
}

const toggleLiteMode = async (value: boolean) => {
  saving.value = true
  error.value = ''
  try {
    await settingsApi.update({ lite_mode: { enabled: value } })
    liteMode.value = value
    toast.success(`Lite mode ${value ? 'enabled' : 'disabled'}. Rebuilding the process set.`)
  } catch (e) {
    error.value = errorMessage(e, 'Could not update lite mode setting.')
  } finally {
    saving.value = false
  }
}

const toggleBucketBackups = async (value: boolean) => {
  saving.value = true
  error.value = ''
  try {
    await settingsApi.update({ s3: { upload_migration_backups: value } })
    bucketBackups.value = value
    toast.success(`Update backups ${value ? 'go to object storage' : 'stay on the disk'}`)
  } catch (e) {
    error.value = errorMessage(e, 'Could not update the backup setting.')
  } finally {
    saving.value = false
  }
}

const openRestart = () => {
  restartError.value = ''
  restartOpen.value = true
}

const restartBench = async () => {
  restarting.value = true
  restartError.value = ''
  try {
    const data = await benchesApi.restart(session.benchName)
    if (hasApiError(data)) {
      restartError.value = apiErrorMessage(data, 'Could not restart the bench.')
      return
    }
    restartOpen.value = false
    toast.success('Bench restarted')
  } catch (e) {
    restartError.value = errorMessage(e, 'Could not restart the bench.')
  } finally {
    restarting.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    codeEditor.value = data?.bench?.enable_code_editor ?? true
    liteMode.value = Boolean(data?.lite_mode?.enabled)
    liteModeSupported.value = Boolean(data?.lite_mode?.supported)
    hasBucket.value = Boolean(data?.s3?.bucket)
    bucketBackups.value = data?.s3?.upload_migration_backups ?? true
    production.value = Boolean(data?.production?.enabled)
    gunicorn.value = data?.gunicorn
  } catch {
    error.value = 'Could not load settings.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <template v-else>
    <ErrorMessage v-if="error" :message="error" class="mb-4" />

    <div class="-mx-3 -mt-3.5 inset-dividers">
      <SettingsRow
        v-if="liteModeSupported"
        label="Lite mode"
        description="One process for web, realtime and jobs. Less memory, best for small sites."
        interactive
        @click="!saving && toggleLiteMode(!liteMode)"
      >
        <Switch
          :model-value="liteMode"
          :disabled="saving"
          @click.stop
          @update:model-value="toggleLiteMode"
        />
      </SettingsRow>

      <SettingsRow
        label="Code editor"
        description="Browse and edit the files of installed apps."
        interactive
        @click="!saving && toggleCodeEditor(!codeEditor)"
      >
        <Switch
          :model-value="codeEditor"
          :disabled="saving"
          @click.stop
          @update:model-value="toggleCodeEditor"
        />
      </SettingsRow>

      <SettingsRow
        v-if="hasBucket"
        label="Keep update backups in object storage"
        description="Pilot backs up sites to the bucket before an update, not to the disk."
        interactive
        @click="!saving && toggleBucketBackups(!bucketBackups)"
      >
        <Switch
          :model-value="bucketBackups"
          :disabled="saving"
          @click.stop
          @update:model-value="toggleBucketBackups"
        />
      </SettingsRow>

      <section v-if="gunicorn && !liteMode" class="px-3 py-3.5">
        <h4 class="text-base-medium text-ink-gray-8">Web workers</h4>

        <p class="mt-1 mb-3 text-ink-gray-6 text-p-sm">
          Gunicorn processes that serve requests. Leave workers blank for one per CPU.
        </p>

        <WebWorkers :settings="gunicorn" />
      </section>

      <section class="px-3 py-3.5">
        <h4 class="text-base-medium text-ink-gray-8">Background workers</h4>

        <p class="mt-1 mb-3 text-ink-gray-6 text-p-sm">
          Configure background worker groups and queues.
        </p>

        <Workers :key="String(liteMode)" />
      </section>

      <section class="px-3 py-3.5">
        <CommonSiteConfig />
      </section>

      <SettingsRow
        label="Bench processes"
        :description="
          production
            ? 'Restart web, workers, scheduler and realtime. Sites stop for a few seconds.'
            : 'Restart is for production benches. On a dev bench, stop the runner and run pilot start again.'
        "
      >
        <Button label="Restart" :disabled="!production" @click="openRestart" />
      </SettingsRow>
    </div>
  </template>

  <Dialog
    :model-value="restartOpen"
    title="Restart bench"
    size="sm"
    @update:model-value="(open: boolean) => !restarting && (restartOpen = open)"
  >
    <p class="text-ink-gray-7 text-p-base">
      Web, workers, scheduler and realtime restart. Sites stop for a few seconds, and running
      background jobs stop.
    </p>

    <ErrorMessage v-if="restartError" :message="restartError" class="mt-3" />

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" :disabled="restarting" @click="restartOpen = false">Cancel</Button>

        <Button variant="solid" :loading="restarting" @click="restartBench">Restart bench</Button>
      </div>
    </template>
  </Dialog>
</template>
