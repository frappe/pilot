<script setup lang="ts">
import { onMounted } from 'vue'
import { Alert, Button, ErrorMessage, Select, Spinner, TextInput } from 'frappe-ui'

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

onMounted(load)
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-6">
    <Alert
      v-if="!connected"
      class="border border-outline-gray-2"
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
      class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3"
    >
      <div>
        <p class="font-medium text-ink-gray-8">Connected to {{ bucket }}</p>
        <p class="text-ink-gray-5 text-p-sm">{{ providerLabel }} · Access key {{ accessKey }}</p>
      </div>

      <Button class="flex-1 sm:flex-none" theme="red" :loading="disconnecting" @click="disconnect"
        >Disconnect</Button
      >
    </div>

    <div class="space-y-4">
      <div class="flex sm:flex-row flex-col gap-4">
        <Select label="Provider" v-model="provider" :options="providerOptions" class="w-full" />
        <Select
          label="Region"
          v-model="region"
          :options="regionOptions"
          :disabled="loadingRegions"
          :placeholder="loadingRegions ? 'Loading regions…' : undefined"
          class="w-full"
        />
      </div>

      <TextInput label="Bucket" v-model="bucket" placeholder="storage-bucket" />
      <TextInput
        label="Endpoint URL"
        v-model="endpointUrl"
        placeholder="Optional for AWS, DigitalOcean, and Hetzner"
      />

      <div class="flex sm:flex-row flex-col gap-4">
        <TextInput label="Access Key" v-model="accessKey" placeholder="AKIA…" class="w-full" />
        <TextInput
          label="Secret Key"
          type="password"
          v-model="secretKey"
          :placeholder="secretKeySet ? '••••••••' : 'Secret key'"
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
</template>
