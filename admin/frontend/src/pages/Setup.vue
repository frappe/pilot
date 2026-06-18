<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { Button, FormControl, FormLabel, Password, ErrorMessage, TextInput, Slider } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import { processLine } from '../utils/ansi.js'

const emit = defineEmits(['done'])

const step = ref('passwords')
const error = ref('')
const loading = ref(false)
const taskLines = ref([])
const taskStreaming = ref(false)
const terminal = ref(null)
const benchName = ref('')
const isLinux = ref(true)

const form = ref({
  mariadb_password: '',
  admin_password: '',
  app_repo: 'https://github.com/frappe/frappe',
  app_branch: 'develop',
  volume_enabled: false,
  workers: [{ queues: 'default, short, long', count: 1 }],
  volume_pool: 'bench-pool',
  volume_backing: 'device',
  volume_device: '',
  volume_image_size: '60G',
  volume_benches_reservation: '10G',
  volume_benches_quota: '50G',
  volume_mariadb_reservation: '5G',
  volume_mariadb_quota: '20G',
  production_process_manager: 'none',
  admin_domain: '',
})

function addWorkerGroup() {
  form.value.workers.push({ queues: '', count: 1 })
}

function removeWorkerGroup(i) {
  form.value.workers.splice(i, 1)
}

// ── framework branch dropdown (fetched from the admin backend) ────────────
const branchOptions = ref([])

async function fetchBranches() {
  try {
    const res = await fetch('/api/setup/branches')
    const data = await res.json()
    branchOptions.value = data.branches || []
  } catch {
    branchOptions.value = []
  }
}

// Keep the configured branch selectable even if it isn't in the fetched list,
// so the dropdown never silently blanks out the saved value.
const branchSelectOptions = computed(() => {
  const options = branchOptions.value.map((b) => ({ label: b, value: b }))
  if (form.value.app_branch && !branchOptions.value.includes(form.value.app_branch)) {
    options.unshift({ label: form.value.app_branch, value: form.value.app_branch })
  }
  return options
})

// ── volume smart defaults ─────────────────────────────────────────────────
const CUSTOM_DEVICE = '__custom__'
const availableDevices = ref([])
const customDevice = ref(false)
const sizesTouched = ref(false)

// ── image-size slider, bounded by total rootfs free space ─────────────────
const GIB = 1024 ** 3
const rootfsFreeBytes = ref(0)
const freeGiB = computed(() => Math.floor(rootfsFreeBytes.value / GIB))
const imageSizeMaxGiB = computed(() => Math.max(5, freeGiB.value || 100))
const imageSizeMinGiB = computed(() => Math.min(5, imageSizeMaxGiB.value))
// Raw text input value — typed freely, clamped only on blur
const imageSizeInputValue = ref(String(parseInt(form.value.volume_image_size) || 60))

// Keep text input in sync when the form changes from outside (config load, slider drag)
watch(
  () => form.value.volume_image_size,
  (size) => {
    const n = parseInt(size)
    if (!isNaN(n)) imageSizeInputValue.value = String(n)
  }
)

function onImageSizeBlur() {
  const n = parseInt(imageSizeInputValue.value)
  const clamped = Math.min(
    imageSizeMaxGiB.value,
    Math.max(imageSizeMinGiB.value, isNaN(n) ? imageSizeMinGiB.value : n),
  )
  form.value.volume_image_size = `${clamped}G`
}

const imageSliderModel = computed({
  get() {
    const n = parseInt(form.value.volume_image_size) || imageSizeMinGiB.value
    return [Math.min(imageSizeMaxGiB.value, Math.max(imageSizeMinGiB.value, n))]
  },
  set([n]) {
    form.value.volume_image_size = `${n}G`
  },
})

const deviceOptions = computed(() => [
  ...availableDevices.value.map((d) => ({
    label: `${d.path} (${Math.floor(d.size_bytes / 1024 ** 3)}G${
      d.pool ? `, pool: ${d.pool}` : d.has_signature ? ', stale data' : ''
    })`,
    value: d.path,
  })),
  { label: 'Custom path…', value: CUSTOM_DEVICE },
])
const showDeviceDropdown = computed(() => availableDevices.value.length > 0 && !customDevice.value)

watch(
  () => form.value.volume_device,
  (value) => {
    if (value === CUSTOM_DEVICE) {
      customDevice.value = true
      form.value.volume_device = ''
    }
  }
)

function backingSizeBytes() {
  if (form.value.volume_backing === 'device') {
    const device = availableDevices.value.find((d) => d.path === form.value.volume_device)
    return device ? device.size_bytes : null
  }
  return parseSize(form.value.volume_image_size)
}

// Mirrors the backend policy: quotas 60/40, reservations 10/5 of the backing size.
function applySmartSizes() {
  if (sizesTouched.value) return
  const bytes = backingSizeBytes()
  if (!bytes) return
  const wholeG = (n) => `${Math.max(1, Math.floor(n / 1024 ** 3))}G`
  form.value.volume_benches_quota = wholeG(bytes * 0.6)
  form.value.volume_mariadb_quota = wholeG(bytes * 0.4)
  form.value.volume_benches_reservation = wholeG(bytes * 0.1)
  form.value.volume_mariadb_reservation = wholeG(bytes * 0.05)
}

watch(
  () => [form.value.volume_backing, form.value.volume_device, form.value.volume_image_size],
  applySmartSizes
)

const configSteps = computed(() =>
  isLinux.value && form.value.volume_enabled
    ? ['passwords', 'customize', 'volume']
    : ['passwords', 'customize']
)
const stepNumber = computed(() => configSteps.value.indexOf(step.value) + 1)
const isConfiguring = computed(() => stepNumber.value > 0)
const isTerminal = computed(() => step.value === 'running')
const modalWidthClass = computed(() => {
  if (isTerminal.value) return 'max-w-2xl'
  return 'max-w-lg'
})

const titles = {
  passwords: 'Set up passwords',
  customize: 'Customize your bench',
  volume: 'ZFS volume management',
  running: 'Initializing bench…',
  done: 'Setup complete',
}
const subtitles = {
  volume: 'Choose where the ZFS pool lives — a spare disk or a disk image on the root filesystem',
}
const title = computed(() => titles[step.value] || benchName.value)
const subtitle = computed(() => subtitles[step.value] || null)

onMounted(loadConfig)

async function loadConfig() {
  try {
    const res = await fetch('/api/setup/config')
    const data = await res.json()
    benchName.value = data.bench_name || ''
    isLinux.value = data.is_linux !== false
    availableDevices.value = data.available_devices || []
    rootfsFreeBytes.value = data.rootfs_free_bytes || 0
    for (const key of Object.keys(form.value)) {
      if (data[key] !== undefined) form.value[key] = data[key]
    }
    if (Array.isArray(data.workers) && data.workers.length)
      form.value.workers = data.workers.map(g => ({ queues: (g.queues || []).join(', '), count: g.count }))
    if (data.running_init_task_id) {
      step.value = 'running'
      streamTask(`/api/setup/stream/${data.running_init_task_id}`, onInitDone)
    }
  } catch {}
  fetchBranches()
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json()
}

function streamTask(url, onDone) {
  taskLines.value = []
  taskStreaming.value = true
  const source = new EventSource(url)
  source.onmessage = (e) => {
    taskLines.value.push(processLine(e.data))
    terminal.value?.scrollToBottom()
  }
  source.addEventListener('done', (e) => {
    taskStreaming.value = false
    source.close()
    onDone(parseInt(e.data) === 0)
  })
  source.onerror = () => {
    taskStreaming.value = false
    source.close()
    error.value = 'Lost connection to task stream.'
  }
}

async function nextStep() {
  if (step.value === 'passwords') {
    if (!form.value.mariadb_password || !form.value.admin_password) {
      error.value = 'All password fields are required'
      return
    }
    loading.value = true
    try {
      const { state } = await postJson('/api/setup/validate-mariadb', {
        mariadb_password: form.value.mariadb_password,
      })
      if (state === 'invalid') {
        error.value = 'Incorrect MariaDB root password.'
        return
      }
    } catch {
      // Validation is best-effort; init still guards the password.
    } finally {
      loading.value = false
    }
  }
  error.value = ''
  step.value = configSteps.value[configSteps.value.indexOf(step.value) + 1]
}

function prevStep() {
  error.value = ''
  step.value = configSteps.value[configSteps.value.indexOf(step.value) - 1]
}

async function saveConfig() {
  const data = await postJson('/api/setup/save', form.value)
  if (!data.ok) throw new Error(data.error || 'Failed to save configuration.')
}

async function startInitTask() {
  const data = await postJson('/api/setup/init', {})
  if (!data.ok) throw new Error(data.error || 'Failed to start initialization.')
  return data.task_id
}


function parseSize(value) {
  // Positive integer with a required K/M/G/T/P suffix — no bare numbers, no decimals, no negatives.
  const match = String(value).trim().toUpperCase().match(/^([1-9]\d*)\s*([KMGTP])$/)
  if (!match) return null
  const mult = { K: 1024, M: 1024 ** 2, G: 1024 ** 3, T: 1024 ** 4, P: 1024 ** 5 }[match[2]]
  return parseInt(match[1], 10) * mult
}

function validateVolume() {
  if (!isLinux.value || !form.value.volume_enabled) return null
  if (!form.value.volume_pool) return 'Pool name is required.'
  const sizeHint = 'must be a positive integer with a K/M/G/T suffix (e.g. 10G)'
  let imageSize = null
  if (form.value.volume_backing === 'image') {
    imageSize = parseSize(form.value.volume_image_size)
    if (imageSize === null) return `Image size "${form.value.volume_image_size}" ${sizeHint}.`
  } else if (!form.value.volume_device) {
    return 'Block device is required.'
  }
  const datasets = [
    ['Bench', form.value.volume_benches_reservation, form.value.volume_benches_quota],
    ['MariaDB', form.value.volume_mariadb_reservation, form.value.volume_mariadb_quota],
  ]
  for (const [label, reservation, quota] of datasets) {
    const res = parseSize(reservation)
    const q = parseSize(quota)
    if (res === null) return `${label} reservation "${reservation}" ${sizeHint}.`
    if (q === null) return `${label} quota "${quota}" ${sizeHint}.`
    if (res > q) return `${label} reservation (${reservation}) cannot exceed quota (${quota}).`
    if (imageSize !== null && q > imageSize) {
      return `${label} quota (${quota}) exceeds the disk image size (${form.value.volume_image_size}).`
    }
  }
  return null
}

async function initialize() {
  error.value = ''
  if (form.value.production_process_manager !== 'none' && !form.value.admin_domain.trim()) {
    error.value = 'Admin domain is required for a production process manager.'
    return
  }
  const volumeError = validateVolume()
  if (volumeError) {
    error.value = volumeError
    return
  }
  loading.value = true
  try {
    await saveConfig()
    const taskId = await startInitTask()
    step.value = 'running'
    streamTask(`/api/setup/stream/${taskId}`, onInitDone)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function onInitDone(success) {
  if (!success) {
    error.value = 'Initialization failed. Check the output above and try again.'
    return
  }
  if (form.value.production_process_manager !== 'none') {
    deployProduction()
  } else {
    step.value = 'done'
    shutdownAndPoll()
  }
}

async function deployProduction() {
  try {
    const data = await postJson('/api/setup/setup-production', {})
    if (!data.ok) throw new Error(data.error || 'Failed to start production setup.')
    streamTask(`/api/setup/stream/${data.task_id}`, onProductionDone)
  } catch (e) {
    error.value = e.message
  }
}

function onProductionDone(success) {
  if (!success) {
    error.value = 'Production setup failed. Check the output above and try again.'
    return
  }
  step.value = 'done'
  finishAndRedirectToDomain()
}

async function finishAndRedirectToDomain() {
  try {
    await postJson('/api/setup/finish', {})
  } catch {}
  // The admin now lives behind nginx at its domain, not the local wizard port.
  const url = `https://${form.value.admin_domain.trim()}`
  setTimeout(() => { window.location.href = url }, 2500)
}

async function shutdownAndPoll() {
  try {
    // Asks the standalone wizard server to shut itself down. May legitimately
    // fail (e.g. dev server) — the on-screen instruction covers that case too.
    await postJson('/api/setup/finish', {})
  } catch {}
  pollUntilBenchIsBack()
}

async function pollUntilBenchIsBack() {
  // The wizard server is gone; once `bench start` brings the bench (and its
  // admin process) back, reload into the normal login flow.
  while (true) {
    await new Promise((r) => setTimeout(r, 3000))
    try {
      const res = await fetch('/api/status')
      if (!res.ok) continue
      const data = await res.json()
      if (data.wizard !== true) {
        emit('done')
        return
      }
    } catch {}
  }
}

function backToConfig() {
  error.value = ''
  step.value = configSteps.value[configSteps.value.length - 1]
}
</script>

<template>
  <div class="flex h-screen items-center justify-center bg-surface-gray-2 p-4">
    <div
      class="flex w-full flex-col rounded-xl border border-outline-gray-2 bg-surface-white shadow-sm"
      :class="modalWidthClass"
      style="max-height: calc(100vh - 2rem)"
    >
      <!-- Header -->
      <div class="border-b border-outline-gray-2 px-5 py-4">
        <p v-if="isConfiguring" class="mb-1 text-xs text-ink-gray-4">
          Step {{ stepNumber }} of {{ configSteps.length }}
        </p>
        <h1 class="font-medium text-ink-gray-7">{{ title }}</h1>
        <p v-if="subtitle" class="mt-0.5 text-sm text-ink-gray-4">{{ subtitle }}</p>
      </div>

      <!-- Body -->
      <div class="flex-1 overflow-y-auto p-5">
        <div v-if="step === 'passwords'" class="flex flex-col gap-4">
          <div class="space-y-1.5">
            <FormLabel label="MariaDB root password" />
            <Password v-model="form.mariadb_password" placeholder="root" />
          </div>
          <div class="space-y-1.5">
            <FormLabel label="Admin password" />
            <Password v-model="form.admin_password" placeholder="Choose a password" @keydown.enter="nextStep" />
          </div>
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="step === 'customize'" class="flex flex-col gap-4">
          <FormControl
            type="select"
            label="Frappe branch"
            v-model="form.app_branch"
            :options="branchSelectOptions"
          />
          <FormControl label="Frappe repository" v-model="form.app_repo" />
          <div class="space-y-1.5">
            <FormLabel label="Workers" />
            <p class="text-xs text-ink-gray-5">
              Each group spawns <span class="font-medium">count</span> workers for the listed queues.
            </p>
            <div
              v-for="(group, i) in form.workers"
              :key="i"
              class="grid grid-cols-[1fr_5rem_auto] items-center gap-2"
            >
              <TextInput v-model="group.queues" placeholder="default, short, long" />
              <TextInput v-model.number="group.count" type="number" :min="1" />
              <Button
                variant="ghost"
                icon="trash-2"
                :disabled="form.workers.length === 1"
                @click="removeWorkerGroup(i)"
              />
            </div>
            <Button variant="subtle" icon-left="plus" label="Add group" @click="addWorkerGroup" />
          </div>
          <FormControl
            type="select"
            label="Production process manager"
            v-model="form.production_process_manager"
            :options="[
              { label: 'None — development mode (bench start)', value: 'none' },
              { label: 'Supervisor — bench-owned supervisord, no root needed', value: 'supervisor' },
              { label: 'Systemd — systemctl --user units', value: 'systemd' },
            ]"
          />
          <div v-if="form.production_process_manager !== 'none'">
            <FormControl
              type="text"
              label="Admin domain"
              v-model="form.admin_domain"
              placeholder="my-admin.example.com"
            />
            <p class="mt-1 text-xs text-ink-gray-5">
              After init the bench is deployed to production and reachable at this domain.
            </p>
          </div>
          <FormControl
            v-if="isLinux"
            type="checkbox"
            label="Enable ZFS volume management"
            v-model="form.volume_enabled"
          />
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="step === 'volume'" class="flex flex-col gap-4">
          <FormControl
            type="select"
            label="Storage backing"
            v-model="form.volume_backing"
            :options="[
              { label: 'Dedicated block device', value: 'device' },
              { label: 'Disk image on root filesystem (no spare disk needed)', value: 'image' },
            ]"
          />
          <FormControl label="Pool name" v-model="form.volume_pool" placeholder="bench-pool" />
          <FormControl
            v-if="form.volume_backing === 'device' && showDeviceDropdown"
            type="select"
            label="Block device"
            v-model="form.volume_device"
            :options="deviceOptions"
          />
          <FormControl
            v-else-if="form.volume_backing === 'device'"
            label="Block device"
            v-model="form.volume_device"
            placeholder="/dev/sdb"
          />
          <div v-else-if="form.volume_backing === 'image'" class="space-y-1.5">
            <div class="flex items-center justify-between gap-2">
              <FormLabel label="Image size" />
              <div class="flex items-center gap-1.5">
                <TextInput
                  type="number"
                  class="w-20"
                  :min="imageSizeMinGiB"
                  :max="imageSizeMaxGiB"
                  v-model="imageSizeInputValue"
                  @blur="onImageSizeBlur"
                />
                <span class="text-xs text-ink-gray-5">GB of {{ freeGiB }} GB free</span>
              </div>
            </div>
            <Slider v-model="imageSliderModel" :min="imageSizeMinGiB" :max="imageSizeMaxGiB" :step="1" />
          </div>
          <p v-if="form.volume_backing === 'image'" class="text-xs text-ink-gray-4">
            A preallocated {{ form.volume_image_size }} file will be created at
            /var/lib/bench-zfs/{{ form.volume_pool || 'pool' }}.img and used as the ZFS pool.
          </p>
          <div class="grid grid-cols-2 gap-2">
            <FormControl label="Bench reservation" v-model="form.volume_benches_reservation" @input="sizesTouched = true" />
            <FormControl label="Bench quota" v-model="form.volume_benches_quota" @input="sizesTouched = true" />
          </div>
          <div class="grid grid-cols-2 gap-2">
            <FormControl label="MariaDB reservation" v-model="form.volume_mariadb_reservation" @input="sizesTouched = true" />
            <FormControl label="MariaDB quota" v-model="form.volume_mariadb_quota" @input="sizesTouched = true" />
          </div>
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="isTerminal" class="flex flex-col gap-3">
          <TerminalOutput ref="terminal" :lines="taskLines" :streaming="taskStreaming" />
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="step === 'done'" class="flex flex-col items-center gap-3 py-6 text-center">
          <p class="text-sm text-ink-gray-7">
            Your bench is ready. The setup server is shutting down now.
          </p>
          <p class="text-sm text-ink-gray-5">
            Run
            <code class="rounded bg-surface-gray-2 px-1 font-mono">bench start</code>
            in your terminal to start your bench — this page will reload automatically once it's back.
          </p>
        </div>
      </div>

      <!-- Footer -->
      <div v-if="(!isTerminal && step !== 'done') || error" class="flex gap-2 border-t border-outline-gray-2 px-5 py-4">
        <Button v-if="stepNumber > 1 && isConfiguring" variant="subtle" class="flex-1" @click="prevStep">
          Back
        </Button>
        <Button v-if="isTerminal && error" variant="subtle" class="w-full" @click="backToConfig">
          Back to configuration
        </Button>
        <Button v-else-if="step === 'passwords'" variant="solid" :loading="loading" class="w-full" @click="nextStep">
          Next
        </Button>
        <Button v-else-if="step !== configSteps[configSteps.length - 1] && isConfiguring" variant="solid" class="flex-1" @click="nextStep">
          Next
        </Button>
        <Button v-else-if="isConfiguring" variant="solid" :loading="loading" class="flex-1" @click="initialize">
          Initialize
        </Button>
      </div>
    </div>
  </div>
</template>
