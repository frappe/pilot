<template>
  <div>
    <p class="font-semibold text-ink-gray-8 text-base">Cloudflare Tunnel</p>
    
    <div v-if="loading" class="flex justify-center py-6">
      <LoadingText />
    </div>
    
    <div v-else class="mt-2 bg-surface-elevation-1 border border-outline-gray-2 rounded-xl p-4 flex flex-col gap-4">
      <!-- State 1: Tunnel not configured at all -->
      <div v-if="!tunnelConfigured" class="flex items-center gap-3 text-sm text-ink-gray-6">
        <span class="size-5 text-ink-gray-4 lucide-info" />
        <div>
          <p class="font-medium text-ink-gray-8">Cloudflare Tunnel is not configured</p>
          <p class="mt-0.5 text-xs">To expose this site automatically, please set up and enable your Cloudflare Tunnel under the <span @click="openCloudflareSettings" class="text-indigo-600 hover:underline cursor-pointer">Deployment</span> configurations page first.</p>
        </div>
      </div>
      
      <!-- State 2: Tunnel configured but no API Token and no cert -->
      <div v-else-if="!apiTokenConfigured && !certConfigured" class="flex items-center gap-3 text-sm text-ink-gray-6">
        <span class="size-5 text-ink-orange-6 lucide-alert-triangle" />
        <div>
          <p class="font-medium text-ink-gray-8">Cloudflare API Token is missing</p>
          <p class="mt-0.5 text-xs">A Cloudflare API Token or SSO certificate is required to manage routing for new sites. Please configure in the <span @click="openCloudflareSettings" class="text-indigo-600 hover:underline cursor-pointer">Deployment</span> configurations page.</p>
        </div>
      </div>

      
      <!-- State 3: Both configured and ready -->
      <div v-else class="flex flex-col gap-4">
        <div class="flex items-center justify-between">
          <div class="flex flex-col gap-0.5">
            <p class="font-medium text-ink-gray-8 text-sm">{{ siteName }}</p>
            <p class="text-ink-gray-5 text-xs">Manage public routing to this site via your tunnel.</p>
          </div>
          <Badge
            :label="isExposed ? 'Exposed' : 'Private'"
            :theme="isExposed ? 'green' : 'gray'"
            variant="subtle"
            size="sm"
          />
        </div>

        <div v-if="isExposed && exposedDomain" class="bg-green-50 text-green-800 p-3 rounded-lg flex items-center gap-2 text-xs border border-green-200">
          <span class="size-4 text-green-600 lucide-check-circle" />
          <span>This site is live at: <a :href="'https://' + exposedDomain" target="_blank" class="font-semibold underline text-green-950">https://{{ exposedDomain }}</a></span>
        </div>

        <div class="flex justify-end">
          <Button
            :variant="isExposed ? 'danger' : 'solid'"
            :loading="toggling"
            @click="isExposed ? confirmRemove() : showExposeDialog = true"
          >
            {{ isExposed ? 'Remove from Tunnel' : 'Expose via Tunnel' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Expose Domain Dialog -->
    <Dialog v-model="showExposeDialog" :options="{ title: 'Expose Site via Tunnel', size: 'sm' }">
      <template #body-content>
        <div class="flex flex-col gap-4">
          <p class="text-sm text-ink-gray-6">
            Enter the public domain you want to map to <strong>{{ siteName }}</strong>.<br/>
            A DNS CNAME record will be created automatically on Cloudflare.
          </p>
          <FormControl
            v-model="domainInput"
            label="Public Domain"
            type="text"
            placeholder="e.g. test.codenetic.online"
            :autofocus="true"
            @keydown.enter="doExpose"
          />
          <p v-if="domainError" class="text-xs text-red-600">{{ domainError }}</p>
        </div>
      </template>
      <template #actions>
        <div class="flex gap-2 justify-end w-full">
          <Button variant="subtle" @click="showExposeDialog = false">Cancel</Button>
          <Button variant="solid" :loading="toggling" @click="doExpose">Expose Site</Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Badge, Button, Dialog, FormControl, LoadingText, toast } from 'frappe-ui'
import { cloudflareApi } from '@/api/cloudflare'
import { apiErrorMessage } from '@/api/client'

const props = defineProps({ siteName: { type: String, required: true } })

const loading = ref(true)
const toggling = ref(false)
const tunnelConfigured = ref(false)
const apiTokenConfigured = ref(false)
const certConfigured = ref(false)
const isExposed = ref(false)
const exposedDomain = ref('')

const showExposeDialog = ref(false)
const domainInput = ref('')
const domainError = ref('')

async function fetchStatus() {
  try {
    const res = await cloudflareApi.getSite(props.siteName)
    if (res.error) {
      toast.error(apiErrorMessage(res, 'Failed to fetch site tunnel status.'))
    } else {
      tunnelConfigured.value = res.tunnel_configured
      apiTokenConfigured.value = res.api_token_configured
      certConfigured.value = res.cert_configured || false
      isExposed.value = res.exposed
      exposedDomain.value = res.domain || ''
    }
  } catch (err) {
    console.error(err)
  } finally {
    loading.value = false
  }
}

function validateDomain(domain) {
  if (!domain || !domain.trim()) return 'Please enter a domain name.'
  const cleaned = domain.trim().toLowerCase()
  // Basic domain validation: must have at least one dot and valid chars
  if (!/^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+$/.test(cleaned)) {
    return 'Please enter a valid domain name (e.g. test.codenetic.online).'
  }
  return ''
}

async function doExpose() {
  domainError.value = ''
  const domain = domainInput.value.trim().toLowerCase()
  const err = validateDomain(domain)
  if (err) {
    domainError.value = err
    return
  }

  toggling.value = true
  showExposeDialog.value = false
  try {
    const res = await cloudflareApi.exposeSite(props.siteName, true, domain)
    if (res.error) {
      toast.error(apiErrorMessage(res, 'Failed to expose site.'))
    } else {
      toast.success(`Site is now live at https://${domain}`)
      domainInput.value = ''
      await fetchStatus()
    }
  } catch (err) {
    toast.error(err.message)
  } finally {
    toggling.value = false
  }
}

async function confirmRemove() {
  toggling.value = true
  try {
    const res = await cloudflareApi.exposeSite(props.siteName, false, exposedDomain.value)
    if (res.error) {
      toast.error(apiErrorMessage(res, 'Failed to remove site exposure.'))
    } else {
      toast.success('Site exposure removed.')
      await fetchStatus()
    }
  } catch (err) {
    toast.error(err.message)
  } finally {
    toggling.value = false
  }
}

function openCloudflareSettings() {
  window.dispatchEvent(new CustomEvent('open-pilot-settings', { detail: { section: 'cloudflare' } }))
}

onMounted(fetchStatus)
</script>
