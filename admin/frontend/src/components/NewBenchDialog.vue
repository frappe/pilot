<script setup>
import { ref, computed, watch } from 'vue'
import { Button, Dialog, ErrorMessage, FormControl } from 'frappe-ui'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const name = ref('')
const processManager = ref('systemd')
const adminDomain = ref('')
const error = ref('')
const creating = ref(false)
const status = ref('')

// Whether the *current* bench is running in production. A dev bench (started
// with `bench start`) most likely has no systemd/supervisor configured, so
// auto-provisioning a managed bench from the UI would silently fail or confuse.
// In that case we point the user at the CLI instead.
const isProduction = ref(null)

const processManagerOptions = [
  { value: 'systemd', label: 'Systemd', hint: 'Recommended' },
  { value: 'supervisor', label: 'Supervisor', hint: 'Alternative' },
]

async function loadMode() {
  isProduction.value = null
  try {
    const response = await fetch('/api/status')
    if (response.ok) {
      const data = await response.json()
      isProduction.value = data.production === true
    } else {
      isProduction.value = false
    }
  } catch {
    isProduction.value = false
  }
}

watch(show, (open) => {
  if (!open) return
  name.value = ''
  processManager.value = 'systemd'
  adminDomain.value = ''
  error.value = ''
  creating.value = false
  status.value = ''
  loadMode()
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
      // The bench's own (socket-activated) admin serves the wizard at its
      // domain. nginx needs a moment to apply the new routing — redirecting
      // too soon lands on the default site page — so give it a few seconds.
      setTimeout(() => { window.location.href = `http://${data.domain}` }, 3000)
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
              label="Admin domain"
              type="text"
              v-model="adminDomain"
              placeholder="my-admin.example.com"
              @input="error = ''"
              @keyup.enter="createBench"
            />
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
