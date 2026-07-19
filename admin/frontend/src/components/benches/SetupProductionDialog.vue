<script setup>
import { ref, computed, watch } from 'vue'
import { Button, Checkbox, Dialog, ErrorMessage, FormControl, Select } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { benchesApi } from '@/api/benches'

const props = defineProps({
  modelValue: Boolean,
  benchName: String,
})
const emit = defineEmits(['update:modelValue', 'started'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const processManager = ref('systemd')
const adminDomain = ref('')
const adminPrefix = ref('')
const wildcardDomains = ref([])
const selectedSuffix = ref('')
const tls = ref(false)
const letsencryptEmail = ref('')
const error = ref('')
const loading = ref(false)
const submitting = ref(false)

const processManagerOptions = [
  { value: 'systemd', label: 'Systemd', hint: 'Recommended (system service)' },
  { value: 'supervisor', label: 'Supervisor', hint: 'Alternative process manager' },
]

async function loadWildcardDomains() {
  loading.value = true
  try {
    const data = await benchesApi.wildcardDomains()
    wildcardDomains.value = data.domains || []
    selectedSuffix.value = wildcardDomains.value[0] || ''
  } catch {
    wildcardDomains.value = []
  } finally {
    loading.value = false
  }
}

watch([adminPrefix, selectedSuffix], () => {
  if (wildcardDomains.value.length > 0) {
    adminDomain.value = adminPrefix.value.trim()
      ? `${adminPrefix.value.trim()}${selectedSuffix.value}`
      : ''
  }
})

watch(show, (open) => {
  if (!open) return
  processManager.value = 'systemd'
  adminDomain.value = ''
  adminPrefix.value = ''
  tls.value = false
  letsencryptEmail.value = ''
  error.value = ''
  submitting.value = false
  loadWildcardDomains()
})

async function submit() {
  if (wildcardDomains.value.length > 0 && !adminPrefix.value.trim()) {
    error.value = 'Admin domain prefix is required.'
    return
  }
  const domain = adminDomain.value.trim()
  if (!domain) {
    error.value = 'Admin domain is required.'
    return
  }
  if (tls.value && !letsencryptEmail.value.trim()) {
    error.value = "Let's Encrypt contact email is required when TLS is enabled."
    return
  }

  error.value = ''
  submitting.value = true
  try {
    const payload = {
      process_manager: processManager.value,
      admin_domain: domain,
      tls: tls.value,
      letsencrypt_email: tls.value ? letsencryptEmail.value.trim() : null,
    }
    const data = await benchesApi.setupProduction(props.benchName, payload)
    if (data.error) {
      error.value = apiErrorMessage(data, 'Could not configure production setup.')
    } else if (data.task_id) {
      show.value = false
      emit('started', data.task_id)
    }
  } catch (caught) {
    error.value = caught.message || 'Failed to setup production.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Dialog v-model="show" title="Setup Production" size="lg" :showCloseButton="true">
    <template #default>
      <div class="flex flex-col gap-5" @pointerdown.stop>
        <p class="text-ink-gray-6 text-sm leading-relaxed">
          Transition <strong class="text-ink-gray-9">{{ benchName }}</strong> to production. This will configure process management, Nginx, and optional SSL/TLS.
        </p>

        <!-- Process Manager selection -->
        <div>
          <span class="block mb-1.5 text-ink-gray-7 text-p-sm-medium">Process manager</span>
          <div class="gap-2 grid grid-cols-2">
            <button v-for="opt in processManagerOptions" :key="opt.value" type="button"
              class="px-3 py-2.5 border rounded-lg text-left transition-colors" :class="processManager === opt.value
                ? 'border-outline-gray-3 bg-surface-gray-2'
                : 'border-outline-gray-2 hover:bg-surface-gray-1'" @click="processManager = opt.value">
              <span class="block font-medium text-ink-gray-9 text-sm">{{ opt.label }}</span>
              <span class="block text-ink-gray-5 text-xs">{{ opt.hint }}</span>
            </button>
          </div>
        </div>

        <!-- Admin Domain input -->
        <div>
          <template v-if="wildcardDomains.length === 0">
            <FormControl label="Admin domain" type="text" v-model="adminDomain" placeholder="admin.example.com"
              @input="error = ''" @keyup.enter="submit" />
            <p class="bg-surface-gray-2 mt-2 px-2.5 py-2 rounded text-ink-gray-6 text-xs leading-normal">
              Point this domain's DNS A record to this server <strong>before</strong> continuing. Setup will not be reachable until the DNS propagates.
            </p>
          </template>
          <div v-else>
            <span class="block mb-1.5 text-ink-gray-7 text-p-sm-medium">Admin domain</span>
            <div class="flex items-stretch gap-2">
              <FormControl class="flex-1 min-w-0" type="text" v-model="adminPrefix" placeholder="admin-sub"
                @input="error = ''" @keyup.enter="submit" />
              <Select v-if="wildcardDomains.length > 1" class="w-48 shrink-0" v-model="selectedSuffix"
                :options="wildcardDomains.map(d => ({ label: d, value: d }))" />
              <span v-else class="flex items-center text-ink-gray-6 text-sm whitespace-nowrap shrink-0">{{
                wildcardDomains[0]
              }}</span>
            </div>
            <p class="mt-1.5 text-ink-gray-5 text-xs">
              The web address you'll use to open this bench.
            </p>
          </div>
        </div>

        <!-- TLS (HTTPS) checkbox -->
        <div class="flex flex-col gap-2">
          <Checkbox v-model="tls" label="Secure with SSL/TLS (HTTPS) via Let's Encrypt" />
          <p class="pl-6 text-ink-gray-5 text-xs">
            Terminates secure HTTPS requests on Nginx using automated Let's Encrypt certificates.
          </p>
        </div>

        <!-- Let's Encrypt Email input (Conditional) -->
        <div v-if="tls">
          <FormControl label="Let's Encrypt contact email" type="email" v-model="letsencryptEmail" placeholder="admin@example.com"
            @input="error = ''" @keyup.enter="submit" />
        </div>

        <ErrorMessage v-if="error" :message="error" />
      </div>
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="show = false">Cancel</Button>
        <Button variant="solid" :loading="submitting" @click="submit" :disabled="!adminDomain || (wildcardDomains.length > 0 && !adminPrefix.trim())">Configure Production</Button>
      </div>
    </template>
  </Dialog>
</template>
