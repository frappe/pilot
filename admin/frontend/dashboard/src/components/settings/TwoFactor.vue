<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Button, Dialog, ErrorMessage, Spinner, TextInput, toast } from 'frappe-ui'

import QrcodeVue from 'qrcode.vue'

import EmptyState from '@/components/common/EmptyState.vue'
import Table from '@/components/common/Table.vue'
import SettingsRow from '@/components/settings/SettingsRow.vue'

import { twoFactorApi } from '@/api/twoFactor'
import { fmtDateTime } from '@/utils/taskFormat'

const columns = [
  { label: 'Device', key: 'name', class: 'w-48' },
  { label: 'Added', key: 'confirmed_at', class: 'w-36' },
  { label: 'Last used', key: 'last_used_at', class: 'w-36' },
  { label: '', key: 'actions', class: 'w-12' },
]

const loading = ref(true)
const busy = ref(false)
const error = ref('')
const status = ref({
  enabled: false,
  credentials: [],
  recovery_codes_remaining: 0,
  max_devices: 0,
})

const atDeviceLimit = computed(
  () => status.value.max_devices > 0 && status.value.credentials.length >= status.value.max_devices,
)

const fmtTimestamp = (seconds) => (seconds ? fmtDateTime(seconds * 1000) : 'Never')

// A device only counts once its code has been verified; half-finished ones are noise.
const devices = computed(() => status.value.credentials.filter((row) => row.confirmed))

const showAdd = ref(false)
const showCodes = ref(false)
const showRemove = ref(false)
const showRegenerate = ref(false)

const deviceName = ref('')
const otp = ref('')
const enrollment = ref(null)
const codes = ref([])
const removing = ref(null)

// Dismissing the dialog abandons the pending credential, which would otherwise sit in
// the store consuming one of the device slots until it expires.
watch(showAdd, async (open) => {
  if (open || !enrollment.value) return
  const abandoned = enrollment.value
  enrollment.value = null
  try {
    await twoFactorApi.removeDevice(abandoned.name)
  } catch {
    // Nothing to do: it expires on its own, and the user has already moved on.
  }
  await load()
})

const openAdd = () => {
  deviceName.value = ''
  otp.value = ''
  enrollment.value = null
  error.value = ''
  showAdd.value = true
}

const startEnrollment = async () => {
  // Fired on blur and Enter, so guard against re-enrolling an already-named device.
  if (enrollment.value || busy.value || !deviceName.value.trim()) return
  error.value = ''
  busy.value = true
  try {
    enrollment.value = await twoFactorApi.startEnrollment(deviceName.value)
  } catch (e) {
    error.value = e.message || 'Could not start enrollment.'
  } finally {
    busy.value = false
  }
}

const confirmEnrollment = async () => {
  error.value = ''
  busy.value = true
  try {
    const result = await twoFactorApi.confirm(enrollment.value.name, otp.value)
    status.value = result
    // Cleared before closing: the close handler deletes whatever enrollment is still
    // pending, and this one is now confirmed.
    enrollment.value = null
    showAdd.value = false
    if (result.recovery_codes) {
      codes.value = result.recovery_codes
      showCodes.value = true
    }
    toast.success('Device added')
  } catch (e) {
    error.value = e.message || 'Could not verify that code.'
  } finally {
    busy.value = false
  }
}

const promptRemove = (row) => {
  removing.value = row
  error.value = ''
  showRemove.value = true
}

const confirmRemove = async () => {
  error.value = ''
  busy.value = true
  try {
    status.value = await twoFactorApi.removeDevice(removing.value.name)
    showRemove.value = false
    toast.success('Device removed')
  } catch (e) {
    error.value = e.message || 'Could not remove that device.'
  } finally {
    busy.value = false
  }
}

const regenerate = async () => {
  error.value = ''
  busy.value = true
  try {
    const result = await twoFactorApi.regenerateRecoveryCodes()
    codes.value = result.recovery_codes
    showRegenerate.value = false
    showCodes.value = true
    await load()
  } catch (e) {
    error.value = e.message || 'Could not regenerate recovery codes.'
  } finally {
    busy.value = false
  }
}

const downloadCodes = () => {
  const body = `Pilot recovery codes\n\nEach code signs you in once when no device is available.\n\n${codes.value.join('\n')}\n`
  const url = URL.createObjectURL(new Blob([body], { type: 'text/plain' }))
  const link = Object.assign(document.createElement('a'), {
    href: url,
    download: 'pilot-recovery-codes.txt',
  })
  link.click()
  URL.revokeObjectURL(url)
  showCodes.value = false
}

const copy = async (text) => {
  try {
    await navigator.clipboard.writeText(text)
    toast.success('Copied')
  } catch {
    toast.error('Could not copy')
  }
}

const load = async () => {
  try {
    status.value = await twoFactorApi.status()
  } catch (e) {
    toast.error(e.message || 'Could not load two-factor settings.')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-5">
    <div class="flex justify-between items-center">
      <p class="font-medium text-ink-gray-8">
        Devices
        <span class="font-normal text-ink-gray-5">
          ({{ devices.length }} of {{ status.max_devices }})
        </span>
      </p>

      <Button
        v-if="!atDeviceLimit"
        icon-left="lucide-plus"
        @click="openAdd"
        >Add device</Button
      >
    </div>

    <div
      v-if="atDeviceLimit"
      class="bg-surface-amber-1 p-3 border border-outline-amber-2 rounded-6 text-ink-amber-7 text-p-sm"
    >
      All {{ status.max_devices }} device slots are in use. Remove one to enrol another, or share
      an existing device's setup key to add another authenticator app.
    </div>

    <EmptyState
      compact
      v-if="!devices.length"
      icon="lucide-shield"
      title="No devices enrolled"
      description="Sign-in needs only the admin password. Add a device to require a code from an authenticator app as well."
    />

    <Table v-else :columns="columns" :rows="devices" height="max-h-96">
      <template #name="{ row }">
        <span class="block max-w-full text-ink-gray-7 truncate" :title="row.name">
          {{ row.name }}
        </span>
      </template>

      <template #confirmed_at="{ row }">
        <span class="text-ink-gray-6 text-sm">{{ fmtTimestamp(row.confirmed_at) }}</span>
      </template>

      <template #last_used_at="{ row }">
        <span class="text-ink-gray-6 text-sm">{{ fmtTimestamp(row.last_used_at) }}</span>
      </template>

      <template #actions="{ row }">
        <Button
          variant="ghost"
          theme="red"
          icon="lucide-trash-2"
          label="Remove device"
          tooltip="Remove device"
          @click="promptRemove(row)"
        />
      </template>
    </Table>

    <div v-if="status.enabled" class="pt-2 border-t border-outline-gray-1">
      <div class="-mx-2.5">
        <SettingsRow
          label="Recovery codes"
          :description="`${status.recovery_codes_remaining} unused. Use one when no device is available.`"
        >
          <Button @click="showRegenerate = true">Regenerate</Button>
        </SettingsRow>
      </div>
    </div>
  </div>

  <Dialog v-model="showAdd" title="Add device" size="md">
    <div class="space-y-3">
      <TextInput
        v-if="!enrollment"
        v-model="deviceName"
        label="Device name"
        placeholder="My Phone"
        maxlength="40"
        @keydown.enter="startEnrollment"
      />

      <template v-if="enrollment">
        <p class="text-ink-gray-6 text-p-base">
          Scan with Authy, Bitwarden, Microsoft Authenticator or any TOTP app.
        </p>

        <div class="flex justify-center bg-surface-white p-4 rounded-6">
          <QrcodeVue :value="enrollment.provisioning_url" :size="176" level="M" render-as="svg" />
        </div>

        <details class="group">
          <summary
            class="flex items-center gap-1.5 text-ink-gray-6 cursor-pointer select-none"
          >
            <span
              class="size-4 transition-transform group-open:rotate-90 lucide-chevron-right" />
            Can't scan? Enter the key by hand
          </summary>

          <div class="bg-surface-gray-2 mt-2 p-3 rounded-6">
            <p class="font-mono text-ink-gray-8 break-all">{{ enrollment.secret }}</p>
            <button class="mt-1 text-ink-blue-2 text-sm" @click="copy(enrollment.secret)">
              Copy key
            </button>
          </div>
        </details>

        <TextInput v-model="otp" label="Code from the app" placeholder="123456" autofocus />
      </template>
    </div>

    <ErrorMessage v-if="error" :message="error" class="mt-2" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showAdd = false">Cancel</Button>
        <Button
          v-if="!enrollment"
          variant="solid"
          :loading="busy"
          :disabled="!deviceName.trim()"
          @click="startEnrollment"
          >Get QR code</Button
        >
        <Button v-else variant="solid" :loading="busy" :disabled="!otp" @click="confirmEnrollment"
          >Verify</Button
        >
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showCodes" title="Save your recovery codes" size="md">
    <p class="text-ink-gray-7 text-p-base">
      These are shown once. Store them somewhere safe — each one signs you in when no device
      is available, and works only once.
    </p>

    <div class="gap-x-6 gap-y-2 grid grid-cols-2 bg-surface-gray-2 mt-3 px-4 py-3.5 rounded-6">
      <span
        v-for="code in codes"
        :key="code"
        class="font-mono text-ink-gray-8 text-sm text-center"
      >
        {{ code }}
      </span>
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button @click="copy(codes.join('\n'))">Copy all</Button>
        <Button variant="solid" icon-left="lucide-download" @click="downloadCodes">
          Download
        </Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showRemove" title="Remove device" size="md">
    <p class="text-ink-gray-7 text-p-base">
      Remove <strong>{{ removing?.name }}</strong
      >? Its codes stop working. Removing the last device turns two-factor off.
    </p>

    <ErrorMessage v-if="error" :message="error" class="mt-2" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showRemove = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="busy" @click="confirmRemove">Remove</Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showRegenerate" title="Regenerate recovery codes" size="md">
    <p class="text-ink-gray-7 text-p-base">
      This replaces all existing codes, including unused ones. Anything you saved earlier stops
      working.
    </p>

    <ErrorMessage v-if="error" :message="error" class="mt-2" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showRegenerate = false">Cancel</Button>
        <Button variant="solid" :loading="busy" @click="regenerate">Regenerate</Button>
      </div>
    </template>
  </Dialog>
</template>
