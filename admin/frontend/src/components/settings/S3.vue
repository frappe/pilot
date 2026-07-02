<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Alert v-if="!connected" theme="blue" title="Why connect S3?" :dismissible="false">
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">
          Connecting an S3-compatible bucket lets you send offsite backups and disk snapshots. Enter an access key, secret
          key, bucket name, and the provider's endpoint URL below, a new bucket with this name will be provisioned if not present.
        </p>
      </template>
    </Alert>

    <div v-if="connected" class="flex sm:flex-row sm:justify-between sm:items-center flex-col gap-3">
      <div>
        <p class="font-medium text-ink-gray-8 text-sm">Connected to {{ bucket }}</p>
        <p class="text-ink-gray-5 text-p-sm">S3 · Access key {{ accessKey }}</p>
      </div>
      <Button class="flex-1 sm:flex-none" variant="subtle" theme="red" :loading="disconnecting"
        @click="disconnect">Disconnect</Button>
    </div>

    <div class="space-y-4">
      <FormControl label="Access Key" type="text" v-model="accessKey" placeholder="AKIA…" />
      <FormControl label="Secret Key" type="password" v-model="secretKey"
        :placeholder="secretKeySet ? '••••••••' : 'Secret access key'" />
      <FormControl label="Bucket" type="text" v-model="bucket" placeholder="storage-bucket" />
      <FormControl label="Endpoint URL" type="text" v-model="endpointUrl" placeholder="https://s3.amazonaws.com" />
      <ErrorMessage v-if="error" :message="error" />
      <div class="flex justify-end">
        <Button variant="solid" :loading="saving" @click="save">
          {{ connected ? 'Update' : 'Connect' }}
        </Button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Alert, Button, ErrorMessage, FormControl, toast } from 'frappe-ui'
import { settingsApi } from '@/api/settings'

const loading = ref(true)
const saving = ref(false)
const disconnecting = ref(false)
const error = ref('')
const accessKey = ref('')
const secretKey = ref('')
const bucket = ref('')
const endpointUrl = ref('')
const secretKeySet = ref(false)

const connected = computed(() => Boolean(accessKey.value && bucket.value && secretKeySet.value))

async function load() {
  loading.value = true
  try {
    const data = await settingsApi.get()
    const s3 = data.s3 || {}
    accessKey.value = s3.access_key || ''
    bucket.value = s3.bucket || ''
    endpointUrl.value = s3.endpoint_url || ''
    secretKeySet.value = !!s3.secret_key_set
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!accessKey.value.trim() || !bucket.value.trim() || !endpointUrl.value.trim()) {
    error.value = 'Access key, bucket, and endpoint URL are required.'
    return
  }
  if (!secretKeySet.value && !secretKey.value.trim()) {
    error.value = 'Secret key is required.'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const result = await settingsApi.update({
      s3: {
        access_key: accessKey.value.trim(),
        secret_key: secretKey.value.trim(),
        bucket: bucket.value.trim(),
        endpoint_url: endpointUrl.value.trim(),
      },
    })
    if (result.ok) {
      secretKey.value = ''
      toast.success('S3 settings saved')
      await load()
    } else {
      error.value = result.error || 'Could not save S3 settings.'
    }
  } catch (e) {
    error.value = e.message || 'Could not save S3 settings.'
  } finally {
    saving.value = false
  }
}

async function disconnect() {
  disconnecting.value = true
  try {
    const result = await settingsApi.update({ s3: { disconnect: true } })
    if (result.ok) {
      accessKey.value = ''
      secretKey.value = ''
      bucket.value = ''
      endpointUrl.value = ''
      secretKeySet.value = false
      toast.success('S3 disconnected')
    } else {
      toast.error(result.error || 'Could not disconnect S3.')
    }
  } catch (e) {
    toast.error(e.message || 'Could not disconnect S3.')
  } finally {
    disconnecting.value = false
  }
}

onMounted(load)
</script>
