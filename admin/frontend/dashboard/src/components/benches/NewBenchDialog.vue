<script setup>
import { ref, computed, watch } from 'vue'
import { Button, Dialog, ErrorMessage, FormControl, LoadingIndicator, Select } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { authApi } from '@/api/auth'
import { benchesApi } from '@/api/benches'

const PM_LABELS = { systemd: 'Systemd', supervisor: 'Supervisor' }

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const name = ref('')
// The host's native production manager.
const nativeProcessManager = ref('systemd')
const processManager = ref('systemd')
const adminDomain = ref('')
const adminPrefix = ref('')
const wildcardDomains = ref([])
const selectedSuffix = ref('')
const error = ref('')
const creating = ref(false)
const status = ref('')
// Post-create waiting state: the bench exists; we poll until its wizard answers.
const provisioning = ref(false)
const wizardUrl = ref('')
const elapsed = ref(0)
let elapsedTimer = null

const elapsedLabel = computed(() => {
  const m = Math.floor(elapsed.value / 60)
  const s = String(elapsed.value % 60).padStart(2, '0')
  return `${m}:${s}`
})

function openWizard() {
  if (wizardUrl.value) window.location.href = wizardUrl.value
}

// Manual button: open in a new tab so this admin page stays put (the automatic
// redirect on ready navigates the current tab instead).
function openWizardInNewTab() {
  if (wizardUrl.value) window.open(wizardUrl.value, '_blank', 'noopener')
}

function stopElapsed() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
}

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
    const data = await authApi.bootstrap()
    isProduction.value = data.production === true
    nativeProcessManager.value = data.native_process_manager || 'systemd'
    processManager.value = nativeProcessManager.value
  } catch {
    isProduction.value = false
  }
}

async function loadWildcardDomains() {
  try {
    const data = await benchesApi.wildcardDomains()
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
  stopElapsed()
  if (!open) { provisioning.value = false; return }
  name.value = ''
  processManager.value = nativeProcessManager.value
  adminDomain.value = ''
  adminPrefix.value = ''
  error.value = ''
  creating.value = false
  status.value = ''
  provisioning.value = false
  wizardUrl.value = ''
  elapsed.value = 0
  loadMode()
  loadWildcardDomains()
})

function startProvisioning(url) {
  provisioning.value = true
  wizardUrl.value = url
  elapsed.value = 0
  stopElapsed()
  elapsedTimer = setInterval(() => { elapsed.value += 1 }, 1000)
}

// Don't redirect before this, even when everything reports ready — gives DNS a
// moment to reach the browser's own resolver beyond the public one DoH queries.
const MIN_WAIT_SECONDS = 30
// A DoH resolver can serve a cached negative for a freshly-created record; past
// this point stop letting that block us (the server already confirmed the bench).
const MAX_WAIT_SECONDS = 120

// A direct probe of the http wizard is mixed-content blocked from this https page,
// so resolve via DoH instead: an HTTPS, CORS-enabled lookup that isn't blocked.
// cache: 'no-store' (plus a nonce) keeps the browser from reusing a stale answer.
// Returns true if the A record is published (and points here when we know our IP),
// false if it resolves but not yet, null if the lookup itself couldn't run.
async function dnsResolved(domain, expectedIp) {
  try {
    const url = `https://dns.google/resolve?name=${domain}&type=A&_=${elapsed.value}`
    const response = await fetch(url, { headers: { accept: 'application/dns-json' }, cache: 'no-store' })
    const aRecords = ((await response.json()).Answer || []).filter((a) => a.type === 1)
    if (!aRecords.length) return false
    return expectedIp ? aRecords.some((a) => a.data === expectedIp) : true
  } catch {
    return null
  }
}

// Poll until the wizard is ready, then send the user to it. Three gates: the
// server confirms nginx routes the bench (loopback, DNS-free), DoH confirms the
// domain has propagated, and a minimum wait elapses. The dev/port flow has no
// domain, so it skips DoH and the wait. DoH being unreachable (null) doesn't
// block, and a cached negative stops blocking after MAX_WAIT_SECONDS.
async function pollReady(params, domain = '', serverIp = '') {
  if (!provisioning.value) return
  let serverReady = false
  try {
    serverReady = (await benchesApi.ready(params)).ready
  } catch { }

  const dns = domain ? await dnsResolved(domain, serverIp) : true
  const minWaited = !domain || elapsed.value >= MIN_WAIT_SECONDS
  const dnsOk = dns !== false || elapsed.value >= MAX_WAIT_SECONDS
  if (serverReady && minWaited && dnsOk) {
    stopElapsed()
    status.value = 'Ready, opening setup…'
    openWizard()
    return
  }
  // 5s between cycles: each one hits the public DoH resolver, which rate-limits
  // (and can ban) aggressive callers, so keep the cadence gentle.
  setTimeout(() => pollReady(params, domain, serverIp), 5000)
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
    const data = await benchesApi.create({ name: benchName, process_manager: processManager.value, admin_domain: domain })
    if (data.error) {
      error.value = apiErrorMessage(data, 'Could not create bench.')
      creating.value = false
      return
    }
    status.value = 'Bench created, bringing up setup…'
    if (data.wizard_at_domain && data.domain) {
      // The bench's own (socket-activated) admin serves the wizard at its domain,
      // over whichever scheme nginx reports it's actually serving (http until a
      // cert is in place). Poll until it answers, then redirect to that scheme.
      const scheme = data.scheme || 'http'
      startProvisioning(`${scheme}://${data.domain}`)
      pollReady({ domain: data.domain, scheme }, data.domain, data.server_ip || '')
    } else {
      // Dev parent: standalone wizard on this host's raw port.
      startProvisioning(`${window.location.protocol}//${window.location.hostname}:${data.port}`)
      pollReady({ port: data.port })
    }
  } catch {
    error.value = 'Failed to create bench'
    creating.value = false
  }
}
</script>

<template>
  <Dialog v-model="show" :title="provisioning ? 'Setting Up Bench' : 'New Bench'" size="lg" :showCloseButton="true">
    <template #default>
      <div class="flex flex-col gap-5">
        <!-- Provisioning: the bench exists; wait until its wizard answers. -->
        <div v-if="provisioning" class="flex flex-col items-center gap-5 py-8 text-center">
          <LoadingIndicator class="w-10 h-10 text-ink-gray-5" />
          <div class="flex flex-col gap-2">
            <p class="font-semibold text-ink-gray-9 text-lg">This may take a few minutes</p>
            <p class="max-w-xs text-ink-gray-6 text-sm">Opens automatically when ready.</p>
          </div>
          <span class="bg-surface-gray-2 px-2.5 py-1 rounded-full font-medium text-ink-gray-6 text-xs">
            Elapsed {{ elapsedLabel }}
          </span>
          <Button variant="subtle" @click="openWizardInNewTab">Open setup now</Button>
        </div>

        <!-- Loading -->
        <div v-else-if="isProduction === null" class="flex flex-col justify-center items-center gap-3 py-16">
          <LoadingIndicator class="w-6 h-6 text-ink-gray-5" />
        </div>

        <!-- Dev bench: guide to the CLI rather than auto-provisioning a
             managed bench the host probably can't run. -->
        <div v-else-if="isProduction === false" class="flex flex-col gap-3">
          <p class="text-ink-gray-7 text-sm">
            This bench is running in development mode, so new benches can be
            created from the command line :
          </p>
          <pre
            class="bg-surface-gray-2 px-3 py-2.5 rounded-lg text-ink-gray-8 text-sm select-all">bench new my-bench</pre>
        </div>

        <!-- Production bench: a process manager is configured, so we create the
             bench and route its domain to the setup wizard. -->
        <template v-else-if="isProduction === true">
          <FormControl label="Bench name" type="text" v-model="name" placeholder="my-bench" @input="error = ''"
            @keyup.enter="createBench" />
          <div>
            <span class="block mb-1.5 text-ink-gray-5 text-xs">Process manager</span>
            <div class="gap-2 grid grid-cols-2">
              <button v-for="opt in processManagerOptions" :key="opt.value" type="button"
                class="px-3 py-2 border rounded-lg text-left transition-colors" :class="processManager === opt.value
                  ? 'border-outline-gray-3 bg-surface-gray-2'
                  : 'border-outline-gray-2 hover:bg-surface-gray-1'" @click="processManager = opt.value">
                <span class="block font-medium text-ink-gray-9 text-sm">{{ opt.label }}</span>
                <span class="block text-ink-gray-5 text-xs">{{ opt.hint }}</span>
              </button>
            </div>
          </div>
          <div>
            <template v-if="wildcardDomains.length === 0">
              <FormControl label="Admin domain" type="text" v-model="adminDomain" placeholder="my-admin.example.com"
                @input="error = ''" @keyup.enter="createBench" />
              <p class="bg-surface-gray-2 mt-1.5 px-2.5 py-2 rounded text-ink-gray-6 text-xs">
                Point this domain's DNS A record to this server <b>before</b> creating the
                bench. It isn't provisioned automatically, so setup can't be reached until
                it resolves here.
              </p>
            </template>
            <div v-else>
              <span class="block mb-1.5 text-ink-gray-5 text-xs">Admin domain</span>
              <div class="flex items-stretch gap-2">
                <FormControl class="flex-1 min-w-0" type="text" v-model="adminPrefix" placeholder="my-admin"
                  @input="error = ''" @keyup.enter="createBench" />
                <Select v-if="wildcardDomains.length > 1" class="w-48 shrink-0" v-model="selectedSuffix"
                  :options="wildcardDomains.map(d => ({ label: d, value: d }))" />
                <span v-else class="flex items-center text-ink-gray-6 text-sm whitespace-nowrap shrink-0">{{
                  wildcardDomains[0]
                  }}</span>
              </div>
            </div>
            <p class="mt-1.5 text-ink-gray-5 text-xs">
              The web address you'll use to open this bench.
            </p>
          </div>
          <ErrorMessage v-if="error" :message="error" />
          <p v-if="status" class="text-ink-gray-6 text-sm">{{ status }}</p>
        </template>
      </div>
    </template>
    <template #actions v-if="!provisioning">
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="show = false">
          {{ isProduction === false ? 'Close' : 'Cancel' }}
        </Button>
        <Button v-if="isProduction === true" variant="solid" :loading="creating" @click="createBench">Create</Button>
      </div>
    </template>
  </Dialog>
</template>
