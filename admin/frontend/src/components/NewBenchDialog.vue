<script setup>
import { ref, computed, watch } from 'vue'
import { Button, Dialog, ErrorMessage, FormControl, Select } from 'frappe-ui'

const PM_LABELS = { systemd: 'Systemd', openrc: 'OpenRC', supervisor: 'Supervisor' }

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const name = ref('')
// The host's native production manager: 'openrc' on Alpine, 'systemd' elsewhere.
const nativeProcessManager = ref('systemd')
const processManager = ref('systemd')
const adminDomain = ref('')
const adminPrefix = ref('')
const wildcardDomains = ref([])
const selectedSuffix = ref('')
const error = ref('')
const creating = ref(false)
const status = ref('')

// Whether the *current* bench is running in production. A dev bench (started
// with `bench start`) most likely has no systemd/supervisor configured, so
// auto-provisioning a managed bench from the UI would silently fail or confuse.
// In that case we point the user at the CLI instead.
const isProduction = ref(null)

// Native manager is recommended; supervisor is the cross-platform alternative.
const processManagerOptions = computed(() => [
  { value: nativeProcessManager.value, label: PM_LABELS[nativeProcessManager.value] || nativeProcessManager.value, hint: 'Recommended' },
  { value: 'supervisor', label: 'Supervisor', hint: 'Alternative' },
])

async function loadMode() {
  isProduction.value = null
  try {
    const response = await fetch('/api/status')
    if (response.ok) {
      const data = await response.json()
      isProduction.value = data.production === true
      nativeProcessManager.value = data.native_process_manager || 'systemd'
      processManager.value = nativeProcessManager.value
    } else {
      isProduction.value = false
    }
  } catch {
    isProduction.value = false
  }
}

async function loadWildcardDomains() {
  try {
    const response = await fetch('/api/benches/wildcard-domains')
    const data = await response.json()
    wildcardDomains.value = data.domains || []
    selectedSuffix.value = wildcardDomains.value[0] || ''
  } catch {
    wildcardDomains.value = []
  }
}

// In wildcard mode the visible field is just the prefix; keep adminDomain (what
// createBench() actually submits) assembled from prefix + chosen suffix.
watch([adminPrefix, selectedSuffix], () => {
  if (wildcardDomains.value.length > 0) {
    adminDomain.value = `${adminPrefix.value.trim()}${selectedSuffix.value}`
  }
})

watch(show, (open) => {
  if (!open) return
  name.value = ''
  processManager.value = nativeProcessManager.value
  adminDomain.value = ''
  adminPrefix.value = ''
  error.value = ''
  creating.value = false
  status.value = ''
  loadMode()
  loadWildcardDomains()
})

// Wait for the new bench's setup-wizard server to come up, then send the user
// to it: a production parent routes the bench's own domain to the wizard, while
// a dev parent is reached on this host's raw port.
async function waitUntilLive(port, target, attempt = 0) {
  try {
    const response = await fetch(`/api/benches/ready?port=${port}`)
    if (response.ok && (await response.json()).ready) {
      status.value = 'Redirecting you to setup…'
      window.location.href = target
      return
    }
  } catch { }
  if (attempt >= 60) {
    error.value = 'New bench setup server did not come up in time.'
    creating.value = false
    return
  }
  setTimeout(() => waitUntilLive(port, target, attempt + 1), 1000)
}

// Poll until the new admin domain's DNS record resolves, then redirect to its
// wizard. DNS propagation can lag the record's creation by a while.
async function waitForDomain(domain, attempt = 0) {
  status.value = 'Waiting for DNS to propagate…'
  try {
    const response = await fetch(`/api/benches/ready?domain=${encodeURIComponent(domain)}`)
    if (response.ok && (await response.json()).ready) {
      status.value = 'Redirecting you to setup…'
      window.location.href = `http://${domain}`
      return
    }
  } catch { }
  if (attempt >= 120) {
    error.value = `DNS for ${domain} did not propagate in time. Open it once it resolves.`
    creating.value = false
    return
  }
  setTimeout(() => waitForDomain(domain, attempt + 1), 2000)
}

async function createBench() {
  const benchName = name.value.trim()
  if (!benchName) return
  if (!/^[a-zA-Z0-9_-]+$/.test(benchName)) {
    error.value = "Bench name must contain only letters, numbers, '-' and '_'"
    return
  }
  const domain = adminDomain.value.trim()
  if (!domain) {
    error.value = 'Admin domain is required so the bench is reachable.'
    return
  }
  error.value = ''
  creating.value = true
  try {
    const response = await fetch('/api/benches/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: benchName, process_manager: processManager.value, admin_domain: domain }),
    })
    const data = await response.json()
    if (!response.ok) {
      error.value = data.error || 'Failed to create bench'
      creating.value = false
      return
    }
    status.value = 'Bench created — opening setup…'
    if (data.wizard_at_domain && data.domain) {
      // The bench's own (socket-activated) admin serves the wizard at its domain.
      // Wait for the new DNS record to resolve before redirecting — landing too
      // soon fails to reach the host.
      waitForDomain(data.domain)
    } else {
      // Dev parent: standalone wizard on this host's raw port.
      waitUntilLive(data.port, `${window.location.protocol}//${window.location.hostname}:${data.port}`)
    }
  } catch {
    error.value = 'Failed to create bench'
    creating.value = false
  }
}
</script>

<template>
  <Dialog v-model="show" title="New Bench" size="lg" :showCloseButton="true">
    <template #default>
      <!-- Stop pointerdown from reaching reka-ui's DismissableLayer, which
           otherwise hijacks focus and prevents a click from focusing inputs
           (keyboard/Tab is unaffected) — same guard SettingsModal uses. -->
      <div class="flex flex-col gap-5" @pointerdown.stop>
        <!-- Dev bench: guide to the CLI rather than auto-provisioning a
             managed bench the host probably can't run. -->
        <div v-if="isProduction === false" class="flex flex-col gap-3">
          <p class="text-sm text-ink-gray-7">
            This bench is running in development mode, so new benches can be
            created from the command line :
          </p>
          <pre class="rounded-lg bg-surface-gray-2 px-3 py-2.5 text-sm text-ink-gray-8 select-all">bench new my-bench</pre>
        </div>

        <!-- Production bench: a process manager is configured, so we create the
             bench and route its domain to the setup wizard. -->
        <template v-else-if="isProduction === true">
          <FormControl
            label="Bench name"
            type="text"
            v-model="name"
            placeholder="my-bench"
            @input="error = ''"
            @keyup.enter="createBench"
          />
          <div>
            <span class="mb-1.5 block text-xs text-ink-gray-5">Process manager</span>
            <div class="grid grid-cols-2 gap-2">
              <button
                v-for="opt in processManagerOptions"
                :key="opt.value"
                type="button"
                class="rounded-lg border px-3 py-2 text-left transition-colors"
                :class="processManager === opt.value
                  ? 'border-outline-gray-3 bg-surface-gray-2'
                  : 'border-outline-gray-2 hover:bg-surface-gray-1'"
                @click="processManager = opt.value"
              >
                <span class="block text-sm font-medium text-ink-gray-9">{{ opt.label }}</span>
                <span class="block text-xs text-ink-gray-5">{{ opt.hint }}</span>
              </button>
            </div>
          </div>
          <div>
            <FormControl
              v-if="wildcardDomains.length === 0"
              label="Admin domain"
              type="text"
              v-model="adminDomain"
              placeholder="my-admin.example.com"
              @input="error = ''"
              @keyup.enter="createBench"
            />
            <div v-else>
              <span class="mb-1.5 block text-xs text-ink-gray-5">Admin domain</span>
              <div class="flex items-stretch gap-2">
                <FormControl
                  class="min-w-0 flex-1"
                  type="text"
                  v-model="adminPrefix"
                  placeholder="my-admin"
                  @input="error = ''"
                  @keyup.enter="createBench"
                />
                <Select v-if="wildcardDomains.length > 1" class="w-48 shrink-0" v-model="selectedSuffix"
                  :options="wildcardDomains.map(d => ({ label: d, value: d }))" />
                <span v-else class="flex shrink-0 items-center whitespace-nowrap text-sm text-ink-gray-6">{{ wildcardDomains[0] }}</span>
              </div>
            </div>
            <p class="mt-1.5 text-xs text-ink-gray-5">
              The web address you'll use to open this bench.
            </p>
          </div>
          <ErrorMessage v-if="error" :message="error" />
          <p v-if="status" class="text-sm text-ink-gray-6">{{ status }}</p>
        </template>
      </div>
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="show = false">
          {{ isProduction === false ? 'Close' : 'Cancel' }}
        </Button>
        <Button v-if="isProduction === true" variant="solid" :loading="creating" @click="createBench">Create</Button>
      </div>
    </template>
  </Dialog>
</template>
