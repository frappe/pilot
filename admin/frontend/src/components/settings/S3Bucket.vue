<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Alert v-if="!connected" theme="blue" title="Why connect S3?" :dismissible="false">
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">
          Connect an S3-compatible bucket to send offsite backups and snapshots.
        </p>
      </template>
    </Alert>

    <div v-if="connected" class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3">
      <div>
        <p class="font-medium text-ink-gray-8 text-sm">Connected to {{ bucket }}</p>
        <p class="text-ink-gray-5 text-p-sm">{{ providerLabel }} · Access key {{ accessKey }}</p>
      </div>
      <Button class="flex-1 sm:flex-none" variant="subtle" theme="red" :loading="disconnecting"
        @click="disconnect">Disconnect</Button>
    </div>

    <div class="space-y-4">
      <FormControl label="Bucket" type="text" v-model="bucket" placeholder="storage-bucket" />
      <div class="flex sm:flex-row flex-col gap-4">
        <Select label="Provider" v-model="provider" :options="providerOptions" class="w-full" />
        <Select label="Region" v-model="region" :options="regionOptions" class="w-full" />
      </div>
      <div class="flex sm:flex-row flex-col gap-4">
        <FormControl label="Access Key" type="text" v-model="accessKey" placeholder="AKIA…" class="w-full" />
        <FormControl label="Secret Key" type="password" v-model="secretKey"
          :placeholder="secretKeySet ? '••••••••' : 'Secret key'" class="w-full" />
      </div>
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
import { computed, onMounted, ref, watch } from 'vue'
import { Alert, Button, ErrorMessage, FormControl, Select, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { settingsApi } from '@/api/settings'

const loading = ref(true)
const saving = ref(false)
const disconnecting = ref(false)
const error = ref('')
const accessKey = ref('')
const secretKey = ref('')
const bucket = ref('')
const provider = ref('')
const region = ref('')
const secretKeySet = ref(false)
const providers = ref([])

const connected = computed(() => Boolean(accessKey.value && bucket.value && secretKeySet.value))
const providerLabel = computed(() => providers.value.find((p) => p.value === provider.value)?.label || provider.value)
const providerOptions = computed(() => providers.value.map((p) => ({ label: p.label, value: p.value })))
const regionOptions = computed(
  () => providers.value.find((p) => p.value === provider.value)?.regions.map((r) => ({ label: r, value: r })) || [],
)

watch(provider, () => {
  if (!regionOptions.value.some((o) => o.value === region.value)) {
    region.value = regionOptions.value[0]?.value || ''
  }
})

async function load() {
  loading.value = true
  try {
    const data = await settingsApi.get()
    providers.value = data.s3_providers || []
    const s3 = data.s3 || {}
    accessKey.value = s3.access_key || ''
    bucket.value = s3.bucket || ''
    provider.value = s3.provider || providers.value[0]?.value || ''
    region.value = s3.region || ''
    secretKeySet.value = !!s3.secret_key_set
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!accessKey.value.trim() || !bucket.value.trim() || !provider.value || !region.value) {
    error.value = 'Access key, bucket, provider, and region are required.'
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
        provider: provider.value,
        region: region.value,
      },
    })
    if (!result.error) {
      secretKey.value = ''
      toast.success('S3 settings saved')
      await load()
    } else {
      error.value = apiErrorMessage(result, 'Could not save S3 settings.')
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
    if (!result.error) {
      accessKey.value = ''
      secretKey.value = ''
      bucket.value = ''
      provider.value = providers.value[0]?.value || ''
      region.value = ''
      secretKeySet.value = false
      toast.success('S3 disconnected')
    } else {
      toast.error(apiErrorMessage(result, 'Could not disconnect S3.'))
    }
  } catch (e) {
    toast.error(e.message || 'Could not disconnect S3.')
  } finally {
    disconnecting.value = false
  }
}

onMounted(load)
</script>
