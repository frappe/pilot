<template>
  <div class="space-y-4">
    <!-- Loading State -->
    <div v-if="loading" class="flex justify-center py-8">
      <LoadingText />
    </div>

    <div v-else class="flex flex-col gap-6">
      <!-- Error Message -->
      <ErrorMessage v-if="error" :message="error" />

      <!-- Status Card -->
      <div class="bg-surface-elevation-1 p-5 border rounded-xl border-outline-gray-2 flex flex-col gap-4">
        <div class="flex items-center justify-between">
          <div class="flex flex-col gap-0.5">
            <h2 class="font-semibold text-ink-gray-9 text-base">Cloudflare Tunnel Status</h2>
            <p class="text-ink-gray-5 text-xs">Expose local services securely without public IP addresses.</p>
          </div>
          <Badge
            :label="tunnelStatus.label"
            :theme="tunnelStatus.theme"
            variant="subtle"
            size="md"
          />
        </div>

        <!-- Connected Status Indicator -->
        <div v-if="statusData.enabled && statusData.status === 'Running'" class="bg-green-50 text-green-800 p-3 rounded-lg flex items-center gap-2 text-sm border border-green-200">
          <span class="size-4 text-green-600 lucide-check-circle" />
          <span><strong>Tunnel Connected!</strong> Your site is being exposed at <strong>https://{{ statusData.domain }}</strong></span>
        </div>

        <div class="flex items-center gap-2 mt-2">
          <Button
            v-if="statusData.token_configured"
            variant="solid"
            :loading="actionLoading"
            @click="toggleTunnel"
          >
            {{ statusData.enabled ? 'Disable Tunnel' : 'Enable Tunnel' }}
          </Button>

          <Button
            v-if="statusData.enabled"
            variant="subtle"
            :loading="actionLoading"
            @click="triggerAction('restart')"
          >
            Restart Tunnel
          </Button>
        </div>
      </div>

      <!-- Configurations Section -->
      <div class="bg-surface-elevation-1 p-5 border rounded-xl border-outline-gray-2 flex flex-col gap-6">
        <div class="flex flex-col gap-0.5">
          <h2 class="font-semibold text-ink-gray-9 text-base">Tunnel Setup</h2>
          <p class="text-ink-gray-5 text-xs">Configure an existing tunnel or create a new one automatically.</p>
        </div>

        <!-- Setup Type Selector -->
        <div class="flex gap-4 border-b border-outline-gray-2 pb-4">
          <label class="flex items-center gap-2 cursor-pointer text-sm font-medium text-ink-gray-7">
            <input type="radio" value="create" v-model="setupType" class="text-indigo-600 focus:ring-indigo-500" />
            Create New Tunnel
          </label>
          <label class="flex items-center gap-2 cursor-pointer text-sm font-medium text-ink-gray-7">
            <input type="radio" value="existing" v-model="setupType" class="text-indigo-600 focus:ring-indigo-500" />
            Your Tunnel Details
          </label>
        </div>

        <!-- Form: Create New -->
        <div v-if="setupType === 'create'" class="flex flex-col gap-4">
          <Password
            label="Cloudflare API Token"
            v-model="createForm.api_token"
            placeholder="Your API Token (needs Zone.DNS & Tunnel permissions)"
          />
          
          <div class="grid grid-cols-2 gap-4">
            <TextInput
              label="Subdomain (e.g. admin)"
              v-model="createForm.subdomain"
              placeholder="Leave blank for root domain"
            />
            
            <div class="flex flex-col gap-1.5">
              <label class="text-ink-gray-7 text-xs">Domain</label>
              <select
                v-model="createForm.domain"
                class="w-full px-3 py-2 bg-surface-elevation-1 border border-outline-gray-2 rounded-lg text-ink-gray-9 text-sm focus:border-indigo-500 focus:ring-indigo-500"
                :disabled="loadingZones || !zones.length"
              >
                <option v-if="loadingZones" value="">Loading domains...</option>
                <option v-else-if="!zones.length" value="">Enter API Token first</option>
                <option v-for="z in zones" :key="z.value" :value="z.value">{{ z.label }}</option>
              </select>
            </div>
          </div>

          <div class="flex justify-end mt-2">
            <Button
              variant="solid"
              :loading="saving"
              :disabled="!isCreateFormValid"
              @click="provisionNewTunnel"
            >
              Provision & Start Tunnel
            </Button>
          </div>
        </div>

        <!-- Tab: Your Tunnel Details -->
        <div v-else class="flex flex-col gap-4">
          <!-- Read-only details when configured and not in edit mode -->
          <div v-if="statusData.token_configured && !isEditingExisting" class="flex flex-col gap-4">
            <div class="grid grid-cols-2 gap-x-4 gap-y-3 text-sm py-2 border-t border-b border-outline-gray-2">
              <div class="text-ink-gray-5">Tunnel Name</div>
              <div class="font-medium text-ink-gray-9 text-right">{{ statusData.tunnel_name || '—' }}</div>

              <div class="text-ink-gray-5">Public Domain</div>
              <div class="font-medium text-ink-gray-9 text-right">
                <a v-if="statusData.domain" :href="'https://' + statusData.domain" target="_blank" class="text-indigo-600 hover:underline">
                  {{ statusData.domain }}
                </a>
                <span v-else>—</span>
              </div>

              <div class="text-ink-gray-5">Tunnel Token</div>
              <div class="font-medium text-ink-green-6 text-right">✓ Configured (Encrypted)</div>

              <div class="text-ink-gray-5">Cloudflare API Token</div>
              <div class="font-medium text-right" :class="statusData.api_token_configured ? 'text-ink-green-6' : 'text-ink-gray-4'">
                {{ statusData.api_token_configured ? '✓ Configured (Encrypted)' : 'Not Configured' }}
              </div>
            </div>

            <div class="flex justify-end gap-2">
              <Button variant="subtle" size="sm" @click="isEditingExisting = true">
                Edit Details
              </Button>
              <Button
                variant="subtle"
                size="sm"
                theme="red"
                :loading="deleting"
                @click="confirmDeleteTunnel"
              >
                Delete Tunnel
              </Button>
            </div>
          </div>

          <!-- Edit form when not configured or editing is active -->
          <div v-else class="flex flex-col gap-4">
            <TextInput
              label="Tunnel Name"
              v-model="existingForm.tunnel_name"
              placeholder="my-tunnel"
            />
            <TextInput
              label="Public Domain"
              v-model="existingForm.domain"
              placeholder="admin.example.com"
            />
            <Password
              label="Tunnel Token"
              v-model="existingForm.tunnel_token"
              placeholder="Cloudflare Tunnel Token"
            />
            <Password
              label="Cloudflare API Token (Optional)"
              v-model="existingForm.api_token"
              placeholder="Cloudflare API Token (Optional, required to expose additional sites)"
            />

            <div class="flex justify-end gap-2 mt-2">
              <Button
                v-if="statusData.token_configured"
                variant="subtle"
                @click="cancelEditingExisting"
              >
                Cancel
              </Button>
              <Button
                variant="solid"
                :loading="saving"
                :disabled="!isExistingFormValid"
                @click="saveExistingSettings"
              >
                Save Configuration
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  Badge,
  Button,
  ErrorMessage,
  TextInput,
  Password,
  LoadingText,
  toast,
} from 'frappe-ui'
import { cloudflareApi } from '@/api/cloudflare'
import { apiErrorMessage } from '@/api/client'

const loading = ref(true)
const saving = ref(false)
const actionLoading = ref(false)
const deleting = ref(false)
const error = ref(null)
const setupType = ref('create')

const statusData = ref({
  status: 'Not Installed',
  enabled: false,
  tunnel_name: '',
  domain: '',
  token_configured: false,
})

const existingForm = ref({
  tunnel_name: '',
  domain: '',
  tunnel_token: '',
  api_token: '',
})

const createForm = ref({
  api_token: '',
  domain: '',
  subdomain: '',
})

const isEditingExisting = ref(false)

function cancelEditingExisting() {
  isEditingExisting.value = false
  existingForm.value.tunnel_name = statusData.value.tunnel_name
  existingForm.value.domain = statusData.value.domain
  if (statusData.value.token_configured) {
    existingForm.value.tunnel_token = '******************************'
  } else {
    existingForm.value.tunnel_token = ''
  }
  if (statusData.value.api_token_configured) {
    existingForm.value.api_token = '******************************'
  } else {
    existingForm.value.api_token = ''
  }
}

const zones = ref([])
const loadingZones = ref(false)

async function loadZones() {
  const token = createForm.value.api_token
  if (!token) return
  const tokenParam = token.startsWith('*****') ? null : token
  
  loadingZones.value = true
  try {
    const res = await cloudflareApi.getZones(tokenParam)
    if (res.error) {
      toast.error(apiErrorMessage(res, 'Failed to fetch domains from Cloudflare.'))
    } else if (res.zones) {
      zones.value = res.zones.map(z => ({ label: z.name, value: z.name }))
      if (zones.value.length && !createForm.value.domain) {
        createForm.value.domain = zones.value[0].value
      }
    }
  } catch (err) {
    console.error('Failed to load zones:', err)
  } finally {
    loadingZones.value = false
  }
}

watch(() => createForm.value.api_token, (val) => {
  if (val) {
    loadZones()
  }
})

const tunnelStatus = computed(() => {
  if (!statusData.value.enabled) {
    return { label: 'Disabled', theme: 'gray' }
  }
  const states = {
    'Running': { label: 'Running', theme: 'green' },
    'Stopped': { label: 'Stopped', theme: 'orange' },
    'Not Installed': { label: 'Not Installed', theme: 'red' },
  }
  return states[statusData.value.status] || { label: statusData.value.status, theme: 'orange' }
})

const isExistingFormValid = computed(() => {
  return (
    existingForm.value.tunnel_name &&
    existingForm.value.domain &&
    existingForm.value.tunnel_token
  )
})

const isCreateFormValid = computed(() => {
  return (
    createForm.value.api_token &&
    createForm.value.domain
  )
})

async function fetchStatus() {
  try {
    const res = await cloudflareApi.get()
    if (res.error) {
      error.value = apiErrorMessage(res)
    } else {
      statusData.value = res
      existingForm.value.tunnel_name = res.tunnel_name
      existingForm.value.domain = res.domain
      if (res.token_configured) {
        existingForm.value.tunnel_token = '******************************'
      }
      if (res.api_token_configured) {
        existingForm.value.api_token = '******************************'
        createForm.value.api_token = '******************************'
      }
    }
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function saveExistingSettings() {
  saving.value = true
  try {
    const res = await cloudflareApi.update(existingForm.value)
    if (res.error) {
      toast.error(apiErrorMessage(res, 'Failed to save settings.'))
    } else {
      toast.success('Configuration saved successfully!')
      isEditingExisting.value = false
      await fetchStatus()
    }
  } catch (err) {
    toast.error(err.message)
  } finally {
    saving.value = false
  }
}

async function provisionNewTunnel() {
  saving.value = true
  try {
    const res = await cloudflareApi.create(createForm.value)
    if (res.error) {
      toast.error(apiErrorMessage(res, 'Failed to provision tunnel.'))
    } else {
      toast.success('Tunnel provisioned and started successfully!')
      // Clear sensitive fields
      createForm.value.api_token = ''
      await fetchStatus()
    }
  } catch (err) {
    toast.error(err.message)
  } finally {
    saving.value = false
  }
}

async function confirmDeleteTunnel() {
  if (!confirm("Are you sure you want to delete this Cloudflare Tunnel? This will stop the service, delete local configuration, and remove the tunnel from your Cloudflare account.")) {
    return
  }
  deleting.value = true
  try {
    const res = await cloudflareApi.delete()
    if (res.error) {
      toast.error(apiErrorMessage(res, 'Failed to delete tunnel.'))
    } else {
      toast.success('Tunnel deleted successfully!')
      isEditingExisting.value = false
      setupType.value = 'create'
      await fetchStatus()
    }
  } catch (err) {
    toast.error(err.message)
  } finally {
    deleting.value = false
  }
}

async function toggleTunnel() {
  actionLoading.value = true
  try {
    const nextState = !statusData.value.enabled
    const res = await cloudflareApi.update({ enabled: nextState })
    if (res.error) {
      toast.error(apiErrorMessage(res, 'Failed to update tunnel state.'))
    } else {
      toast.success(nextState ? 'Tunnel enabled and service started!' : 'Tunnel service stopped and disabled.')
      await fetchStatus()
    }
  } catch (err) {
    toast.error(err.message)
  } finally {
    actionLoading.value = false
  }
}

async function triggerAction(actionName) {
  actionLoading.value = true
  try {
    const res = await cloudflareApi.action(actionName)
    if (res.error) {
      toast.error(apiErrorMessage(res, `Failed to ${actionName} tunnel.`))
    } else {
      toast.success(`Tunnel ${actionName}ed successfully!`)
      await fetchStatus()
    }
  } catch (err) {
    toast.error(err.message)
  } finally {
    actionLoading.value = false
  }
}

onMounted(fetchStatus)
</script>
