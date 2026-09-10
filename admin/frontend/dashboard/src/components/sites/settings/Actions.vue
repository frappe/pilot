<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, ErrorMessage, TextInput } from 'frappe-ui'

import { useSite } from '@/composables/sites/useSite'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  siteName: string
}

const props = defineProps<Props>()
const router = useRouter()

const { site, nginxEnabled } = useSite(props.siteName)

const error = ref('')

const sslLoading = ref(false)
const showSslEmail = ref(false)
const sslEmail = ref('')
const sslEmailError = ref('')

const enableSsl = async (email) => {
  error.value = ''
  sslEmailError.value = ''
  sslLoading.value = true
  try {
    const data = await sitesApi.enableTls(props.siteName, email)
    if (data.task_id) {
      showSslEmail.value = false
      openTaskDetailPage(router, data.task_id)
    } else if (data.error?.details?.needs_email) {
      showSslEmail.value = true
      if (email) sslEmailError.value = apiErrorMessage(data, 'Could not enable SSL.')
    } else {
      error.value = apiErrorMessage(data, 'Could not enable SSL.')
    }
  } catch (e) {
    if (showSslEmail.value) sslEmailError.value = e.message
    else error.value = e.message
  } finally {
    sslLoading.value = false
  }
}

const clearingCache = ref(false)

const clearCache = async () => {
  error.value = ''
  clearingCache.value = true
  try {
    const data = await sitesApi.clearCache(props.siteName)
    if (data.task_id) openTaskDetailPage(router, data.task_id)
    else error.value = apiErrorMessage(data, 'Failed to clear cache.')
  } catch (e) {
    error.value = e.message || 'Failed to clear cache.'
  } finally {
    clearingCache.value = false
  }
}

const refreshingStorage = ref(false)

const refreshStorage = async () => {
  error.value = ''
  refreshingStorage.value = true
  try {
    const data = await sitesApi.refreshStorage(props.siteName)
    if (data.task_id) openTaskDetailPage(router, data.task_id)
    else error.value = apiErrorMessage(data, 'Failed to refresh storage usage.')
  } catch (e) {
    error.value = e.message || 'Failed to refresh storage usage.'
  } finally {
    refreshingStorage.value = false
  }
}

// Each action only appears once its `condition` passes.
const Actions = [
  {
    key: 'enable_ssl',
    label: 'Enable SSL',
    description: "Issue a Let's Encrypt certificate and serve this site over HTTPS.",
    condition: () => nginxEnabled.value && !site.value?.ssl,
    loading: () => sslLoading.value,
    onClick: () => enableSsl(),
  },
  {
    key: 'clear_cache',
    label: 'Clear cache',
    buttonLabel: 'Clear',
    description: "Clear this site's cache if something looks stale.",
    condition: () => true,
    loading: () => clearingCache.value,
    onClick: () => clearCache(),
  },
  {
    key: 'refresh_storage',
    label: 'Refresh usage',
    buttonLabel: 'Refresh',
    description: "Measure this bench's sites again. Usage is collected every six hours.",
    condition: () => true,
    loading: () => refreshingStorage.value,
    onClick: () => refreshStorage(),
  },
]

const rows = computed(() => Actions.filter((row) => row.condition()))
</script>

<template>
  <div v-if="rows.length" class="mt-3">
    <h2 class="mb-3 text-base-semibold text-ink-gray-8">Actions</h2>
    <div
      v-for="row in rows"
      :key="row.key"
      class="flex justify-between items-start gap-x-2.5 py-4 border-b last:border-b-0 border-outline-alpha-gray-1"
    >
      <div class="flex flex-col gap-1">
        <p class="font-medium text-ink-gray-8">{{ row.label }}</p>
        <p class="text-ink-gray-6 text-p-sm">{{ row.description }}</p>
      </div>

      <Button
        class="ml-4 shrink-0"
        :loading="row.loading()"
        @click="row.onClick"
      >
        {{ row.buttonLabel || row.label }}
      </Button>
    </div>

    <ErrorMessage v-if="error" :message="error" class="mt-2" />
  </div>

  <!-- Let's Encrypt email dialog -->
  <Dialog v-model="showSslEmail" title="Enable SSL" size="md">
    <p class="text-ink-gray-7 text-p-sm">
      A Let's Encrypt email is required to issue and renew certificates.
    </p>

    <TextInput
      v-model="sslEmail"
      type="email"
      placeholder="you@example.com"
      class="mt-4 w-full"
      @keydown.enter="enableSsl(sslEmail)"
    >
      <template #label>
        <span class="text-sm">Let's Encrypt email</span>
      </template>
    </TextInput>

    <ErrorMessage v-if="sslEmailError" :message="sslEmailError" class="mt-2" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="outline" @click="showSslEmail = false">Cancel</Button>
        <Button
          variant="solid"
          :loading="sslLoading"
          :disabled="!sslEmail"
          @click="enableSsl(sslEmail)"
        >
          Enable SSL
        </Button>
      </div>
    </template>
  </Dialog>
</template>
