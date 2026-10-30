<script setup lang="ts">
import { Alert, Button, Dialog, ErrorMessage, Select, Spinner, TextInput } from 'frappe-ui'
import { onMounted, ref } from 'vue'

import { useObjectStorage } from '@/composables/settings/useObjectStorage'

const {
  loading,
  saving,
  disconnecting,
  error,
  accessKey,
  secretKey,
  bucket,
  provider,
  region,
  endpointUrl,
  secretKeySet,
  loadingRegions,
  connected,
  providerLabel,
  providerOptions,
  regionOptions,
  canSave,
  load,
  save,
  disconnect,
} = useObjectStorage()

const showDisconnect = ref(false)

const confirmDisconnect = async () => {
  await disconnect()

  showDisconnect.value = false
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-6">
    <Alert
      v-if="!connected"
      theme="blue"
      title="Why connect object storage?"
      :dismissible="false"
    >
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">
          Connect S3-compatible object storage to send offsite backups and snapshots.
        </p>
      </template>
    </Alert>

    <div
      v-if="connected"
      class="flex items-center gap-3 p-4 border border-outline-gray-3 border-dashed rounded-7"
    >
      <span class="place-items-center grid bg-surface-gray-2 rounded-full size-10 shrink-0">
        <span class="size-5 text-ink-gray-8 lucide-hard-drive" aria-hidden="true" />
      </span>

      <div class="flex flex-col flex-1 gap-0.5 min-w-0">
        <p class="flex items-center gap-2 text-base-semibold text-ink-gray-8">
          <span class="truncate">{{ bucket }}</span>

          <span class="flex items-center gap-1.5 text-ink-gray-6 text-sm">
            <span class="bg-surface-green-5 rounded-full size-1.5" />
            Connected
          </span>
        </p>

        <p class="text-ink-gray-5 text-p-sm truncate">
          {{ providerLabel }}
          · Access key {{ accessKey }}
        </p>
      </div>

      <Button
        label="Disconnect"
        theme="red"
        icon-left="lucide-unplug"
        :loading="disconnecting"
        @click="showDisconnect = true"
      />
    </div>

    <div class="space-y-4">
      <div class="flex sm:flex-row flex-col gap-4">
        <Select
          side="bottom"
          label="Provider"
          required
          v-model="provider"
          :options="providerOptions"
          class="w-full"
        />
        <Select
          side="bottom"
          label="Region"
          required
          v-model="region"
          :options="regionOptions"
          :disabled="loadingRegions"
          :placeholder="loadingRegions ? 'Loading regions…' : undefined"
          class="w-full"
        />
      </div>

      <TextInput label="Bucket" required v-model="bucket" placeholder="storage-bucket" />
      <TextInput
        label="Endpoint URL"
        v-model="endpointUrl"
        placeholder="Optional for AWS, DigitalOcean, and Hetzner"
      />

      <div class="flex sm:flex-row flex-col gap-4">
        <TextInput
          label="Access Key"
          required
          v-model="accessKey"
          placeholder="AKIA…"
          class="w-full"
        />
        <TextInput
          label="Secret Key"
          :required="!secretKeySet"
          type="password"
          autocomplete="new-password"
          v-model="secretKey"
          :placeholder="secretKeySet ? '********' : 'Secret key'"
          class="w-full"
        />
      </div>

      <ErrorMessage v-if="error" :message="error" />
      <div class="flex justify-end">
        <Button variant="solid" :loading="saving" :disabled="!canSave" @click="save">
          {{ connected ? 'Update' : 'Connect' }}
        </Button>
      </div>
    </div>
  </div>

  <Dialog v-model="showDisconnect" title="Disconnect object storage" size="sm">
    <p class="text-ink-gray-7 text-p-sm">
      Backups stop going to this bucket until you connect storage again.
    </p>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" label="Cancel" @click="showDisconnect = false" />

        <Button
          variant="solid"
          theme="red"
          label="Disconnect"
          :loading="disconnecting"
          @click="confirmDisconnect"
        />
      </div>
    </template>
  </Dialog>
</template>
