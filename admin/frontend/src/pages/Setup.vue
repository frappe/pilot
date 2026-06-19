<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { Button, FormControl, FormLabel, Password, Slider, ErrorMessage, Progress, FeatherIcon } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import { processLine } from '../utils/ansi.js'

const emit = defineEmits(['done'])

const step = ref('passwords')
const error = ref('')
const loading = ref(false)
const benchName = ref('')
const isLinux = ref(true)
const dedicatedWillInstall = ref(false)  // true when a new dedicated instance will be created
const sharedWillInstall = ref(false)     // true when the system/shared MariaDB isn't installed yet

// Whether init will install + secure MariaDB for the selected mode (and so set
// its root password to the entered value). macOS has no dedicated mode, so it
// always follows the shared/system MariaDB.
const dbWillInstall = computed(() => {
  if (isLinux.value && form.value.dedicated_db === 'dedicated') return dedicatedWillInstall.value
  return sharedWillInstall.value
})
const dbPasswordDescription = computed(() =>
  dbWillInstall.value ? 'MariaDB will be installed and its root password set to this value.' : undefined
)

// ── init-task streaming state ─────────────────────────────────────────────
const taskLines = ref([])
const taskStreaming = ref(false)
const terminal = ref(null)
const progress = ref(0)
const currentStep = ref('Starting…')
const showDetails = ref(false)

const form = ref({
  admin_password: '',
  mariadb_password: '',
  mariadb_admin_user: 'root',
  dedicated_db: 'dedicated',  // 'dedicated' | 'shared' — Linux only, UI-only field
  app_repo: 'https://github.com/frappe/frappe',
  app_branch: 'develop',
  volume_enabled: false,
  volume_pool: 'bench-pool',
  volume_backing: 'image',
  volume_device: '',
  volume_image_size: '60G',
  volume_reservation: '15G',
  volume_quota: '60G',
  production_process_manager: 'none',
  admin_domain: '',
  admin_tls: false,
})

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

// ── storage (volumes) ─────────────────────────────────────────────────────
// Non-technical framing: "this machine's disk" (a disk image) vs "an attached
// disk" (a dedicated block device). Pool name, reservations and quotas are kept
// at smart defaults and never shown — advanced tuning lives in Settings.
const CUSTOM_DEVICE = '__custom__'
const availableDevices = ref([])
const customDevice = ref(false)

const deviceOptions = computed(() => [
  ...availableDevices.value.map((d) => ({
    label: `${d.path} (${Math.floor(d.size_bytes / 1024 ** 3)} GB${d.pool ? ', in use' : d.has_signature ? ', has data' : ''})`,
    value: d.path,
  })),
  { label: 'Other disk…', value: CUSTOM_DEVICE },
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

function parseSize(value) {
  // Positive integer with a required K/M/G/T/P suffix — no bare numbers, no decimals, no negatives.
  const match = String(value).trim().toUpperCase().match(/^([1-9]\d*)\s*([KMGTP])$/)
  if (!match) return null
  const mult = { K: 1024, M: 1024 ** 2, G: 1024 ** 3, T: 1024 ** 4, P: 1024 ** 5 }[match[2]]
  return parseInt(match[1], 10) * mult
}

function backingSizeBytes() {
  if (form.value.volume_backing === 'device') {
    const device = availableDevices.value.find((d) => d.path === form.value.volume_device)
    return device ? device.size_bytes : null
  }
  return parseSize(form.value.volume_image_size)
}

// ── image size, bounded by free space on this machine's disk ───────────────
const GIB = 1024 ** 3
const rootfsFreeBytes = ref(0)
const freeGiB = computed(() => Math.floor(rootfsFreeBytes.value / GIB))
const imageSizeMaxGiB = computed(() => Math.max(5, freeGiB.value || 100))
const imageSizeMinGiB = computed(() => Math.min(5, imageSizeMaxGiB.value))
const imageSizeGiB = computed(() => parseInt(form.value.volume_image_size) || imageSizeMinGiB.value)

const imageSliderModel = computed({
  get: () => [Math.min(imageSizeMaxGiB.value, Math.max(imageSizeMinGiB.value, imageSizeGiB.value))],
  set: ([n]) => { form.value.volume_image_size = `${n}G` },
})

// Keep the allocation within what's actually free, even if the saved default
// was sized for a larger disk.
function clampImageSize() {
  const clamped = Math.min(imageSizeMaxGiB.value, Math.max(imageSizeMinGiB.value, imageSizeGiB.value))
  form.value.volume_image_size = `${clamped}G`
}

// Mirrors the backend policy: a single dataset per bench — quota = whole
// backing, reservation = 15%. Computed silently so the user never has to think
// about ZFS datasets.
function applySmartSizes() {
  const bytes = backingSizeBytes()
  if (!bytes) return
  const wholeG = (n) => `${Math.max(1, Math.floor(n / 1024 ** 3))}G`
  form.value.volume_quota = wholeG(bytes)
  form.value.volume_reservation = wholeG(bytes * 0.15)
}

watch(() => form.value.dedicated_db, (val) => {
  if (val === 'shared') form.value.volume_enabled = false
  if (val === 'dedicated') form.value.mariadb_admin_user = 'root'
})
// A fresh install can only ever secure the pre-existing 'root' account, so lock
// the root user to 'root' whenever init will install MariaDB itself.
watch(dbWillInstall, (fresh) => {
  if (fresh) form.value.mariadb_admin_user = 'root'
}, { immediate: true })
watch(() => [form.value.volume_backing, form.value.volume_device, form.value.volume_image_size], applySmartSizes)

// ── step flow ──────────────────────────────────────────────────────────────
const configSteps = computed(() => {
  const steps = ['passwords', 'database', 'customize']
  if (isLinux.value && form.value.dedicated_db === 'dedicated' && form.value.volume_enabled)
    steps.push('storage')
  return steps
})
const stepNumber = computed(() => configSteps.value.indexOf(step.value) + 1)
const isConfiguring = computed(() => stepNumber.value > 0)
const isRunning = computed(() => step.value === 'running')
const isLastConfigStep = computed(() => step.value === configSteps.value[configSteps.value.length - 1])
const modalWidthClass = computed(() => (isRunning.value && showDetails.value ? 'max-w-2xl' : 'max-w-lg'))

const titles = {
  passwords: 'Admin password',
  database: 'Database',
  customize: 'Customize your bench',
  storage: 'Storage',
  running: 'Setting up your bench',
  done: 'Setup complete',
}
const subtitles = {
  database: 'Configure your MariaDB connection',
  storage: 'Choose where your bench keeps its data',
}
const title = computed(() => titles[step.value] || benchName.value)
const subtitle = computed(() => subtitles[step.value] || null)

onMounted(() => {
  loadConfig()
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onBeforeUnmount(() => document.removeEventListener('visibilitychange', onVisibilityChange))

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
    clampImageSize()
    if (isLinux.value) form.value.dedicated_db = data.mariadb_instance ? 'dedicated' : 'shared'
    if (data.running_init_task_id) {
      step.value = 'running'
      streamTask(`/api/setup/stream/${data.running_init_task_id}`, onInitDone)
    }
  } catch {}
  fetchBranches()
  checkDedicatedInstall()
}

// Check whether init will install MariaDB fresh for each mode, so the password
// step can tell the user their entered value becomes the new root password.
async function checkDedicatedInstall() {
  try {
    const [dedicated, shared] = await Promise.all([
      postJson('/api/setup/validate-mariadb', { mariadb_password: '', dedicated_db: true }),
      postJson('/api/setup/validate-mariadb', { mariadb_password: '', dedicated_db: false }),
    ])
    dedicatedWillInstall.value = dedicated.state === 'will_install'
    sharedWillInstall.value = shared.state === 'will_install'
  } catch {}
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json()
}

// ── streaming + progress ─────────────────────────────────────────────────
// Init prints `[N/M] description...` per step (see InitCommand). We parse those
// to drive the progress bar; the raw output stays available behind "details".
function updateProgress(raw) {
  const match = raw.match(/^\[(\d+)\/(\d+)\]\s*(.+?)\.*\s*$/)
  if (!match) return
  const [, done, total, label] = match
  progress.value = Math.round((parseInt(done) / parseInt(total)) * 100)
  currentStep.value = label
}

function scrollTerminal() {
  // Skip while the tab is hidden: scrolling against un-painted background-tab
  // layout is what made the terminal jump to a blank area on return.
  if (!document.hidden) terminal.value?.scrollToBottom()
}

function onVisibilityChange() {
  if (!document.hidden) terminal.value?.scrollToBottom()
}

function streamTask(url, onDone) {
  taskLines.value = []
  taskStreaming.value = true
  progress.value = 0
  currentStep.value = 'Starting…'
  let volatile = false // last line is an uncommitted \r progress preview
  const source = new EventSource(url)
  source.onmessage = (e) => {
    if (volatile) { taskLines.value.pop(); volatile = false }
    taskLines.value.push(processLine(e.data))
    updateProgress(e.data)
    scrollTerminal()
  }
  source.addEventListener('overwrite', (e) => {
    if (volatile) taskLines.value[taskLines.value.length - 1] = processLine(e.data)
    else { taskLines.value.push(processLine(e.data)); volatile = true }
    scrollTerminal()
  })
  source.addEventListener('done', (e) => {
    taskStreaming.value = false
    source.close()
    onDone(parseInt(e.data) === 0)
  })
  source.onerror = () => {
    taskStreaming.value = false
    source.close()
    failWith('Lost connection to the setup process.')
  }
}

function failWith(message) {
  error.value = message
  showDetails.value = true // surface the terminal so the user can see what broke
}

function toggleDetails() {
  showDetails.value = !showDetails.value
  if (showDetails.value) scrollTerminal()
}

// ── navigation ─────────────────────────────────────────────────────────────
async function nextStep() {
  if (step.value === 'passwords') {
    if (!form.value.admin_password) {
      error.value = 'Admin password is required'
      return
    }
  }

  if (step.value === 'database') {
    if (!form.value.mariadb_password) {
      error.value = 'MariaDB password is required'
      return
    }
    // Always validate before leaving the database step. The endpoint reports
    // 'will_install' (fresh — nothing to validate), 'valid', or 'invalid'.
    // dedicated only applies to a Linux dedicated instance; shared system
    // MariaDB (Linux 'shared' and all of macOS) validates the live credentials.
    const dedicated = isLinux.value && form.value.dedicated_db === 'dedicated'
    loading.value = true
    try {
      const { state } = await postJson('/api/setup/validate-mariadb', {
        mariadb_password: form.value.mariadb_password,
        mariadb_admin_user: form.value.mariadb_admin_user,
        dedicated_db: dedicated,
      })
      if (dedicated) dedicatedWillInstall.value = state === 'will_install'
      else sharedWillInstall.value = state === 'will_install'
      if (state === 'invalid') {
        error.value = 'Incorrect MariaDB credentials.'
        return
      }
    } catch {
      // Validation is best-effort against transport errors; init still guards
      // the password. An explicit 'invalid' above always blocks.
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
  const payload = { ...form.value }
  delete payload.dedicated_db
  if (isLinux.value) {
    if (form.value.dedicated_db === 'dedicated') {
      payload.mariadb_instance = benchName.value
      payload.mariadb_socket_path = `/run/mysqld/mysqld-${benchName.value}.sock`
      payload.mariadb_data_dir = `/var/lib/mysql-${benchName.value}`
      payload.mariadb_admin_user = 'root'  // fresh instance always has root; not user-configurable
    } else {
      payload.mariadb_instance = ''
      payload.mariadb_socket_path = ''
      payload.mariadb_data_dir = ''
      payload.volume_enabled = false
      // Shared: the root user is locked to 'root' only for a fresh install;
      // an existing server may use a custom superuser (see the field's watcher).
    }
  }
  const data = await postJson('/api/setup/save', payload)
  if (!data.ok) throw new Error(data.error || 'Failed to save configuration.')
}

function validateStorage() {
  if (!isLinux.value || !form.value.volume_enabled) return null
  if (form.value.volume_backing === 'device' && !form.value.volume_device)
    return 'Please choose an attached disk.'
  return null
}

async function initialize() {
  error.value = ''
  if (form.value.production_process_manager !== 'none' && !form.value.admin_domain.trim()) {
    error.value = 'Admin domain is required for a production process manager.'
    return
  }
  const storageError = validateStorage()
  if (storageError) {
    error.value = storageError
    return
  }
  loading.value = true
  try {
    await saveConfig()
    const data = await postJson('/api/setup/init', {})
    if (!data.ok) throw new Error(data.error || 'Failed to start setup.')
    step.value = 'running'
    streamTask(`/api/setup/stream/${data.task_id}`, onInitDone)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function onInitDone(success) {
  if (!success) {
    failWith('Setup failed. Open the details to see what went wrong, then try again.')
    return
  }
  progress.value = 100
  currentStep.value = 'Done'
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
  progress.value = 100
  currentStep.value = 'Done'
  step.value = 'done'
  finishAndRedirectToDomain()
}

async function finishAndRedirectToDomain() {
  try {
    await postJson('/api/setup/finish', {})
  } catch {}
  // The admin now lives behind nginx at its domain, not the local wizard port.
  const scheme = form.value.admin_tls === true ? 'https' : 'http'
  const url = `${scheme}://${form.value.admin_domain.trim()}`
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
  showDetails.value = false
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
          <Password label="Admin password" v-model="form.admin_password" placeholder="Choose a password" @keydown.enter="nextStep" />
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="step === 'database'" class="flex flex-col gap-4">
          <FormControl
            v-if="isLinux"
            type="select"
            label="Database"
            v-model="form.dedicated_db"
            :options="[
              { label: 'Dedicated instance (recommended)', value: 'dedicated' },
              { label: 'Shared system MariaDB', value: 'shared' },
            ]"
          />
          <FormControl
            v-if="(!isLinux || form.dedicated_db === 'shared') && !dbWillInstall"
            label="MariaDB admin user"
            v-model="form.mariadb_admin_user"
          />
          <Password
            label="MariaDB root password"
            v-model="form.mariadb_password"
            placeholder="password"
            :description="dbPasswordDescription"
            @keydown.enter="nextStep"
          />
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
          <FormControl
            type="select"
            label="Production process manager"
            v-model="form.production_process_manager"
            :options="[
              { label: 'Development — run it yourself', value: 'none' },
              { label: 'Systemd — recommended', value: 'systemd' },
              { label: 'Supervisor — alternative', value: 'supervisor' },
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
            v-if="isLinux && form.dedicated_db === 'dedicated'"
            type="checkbox"
            label="Use volumes (snapshots & backups)"
            v-model="form.volume_enabled"
          />
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="step === 'storage'" class="flex flex-col gap-4">
          <FormControl
            type="select"
            label="Store data on"
            v-model="form.volume_backing"
            :options="[
              { label: 'This machine\'s disk', value: 'image' },
              { label: 'An attached disk', value: 'device' },
            ]"
          />
          <FormControl
            v-if="form.volume_backing === 'device' && showDeviceDropdown"
            type="select"
            label="Attached disk"
            v-model="form.volume_device"
            :options="deviceOptions"
          />
          <FormControl
            v-else-if="form.volume_backing === 'device'"
            label="Attached disk"
            v-model="form.volume_device"
            placeholder="/dev/sdb"
          />
          <div v-else class="space-y-1.5">
            <div class="flex items-baseline justify-between">
              <FormLabel label="Space to allocate" />
              <span class="text-sm text-ink-gray-5">{{ imageSizeGiB }} GB of {{ freeGiB }} GB free</span>
            </div>
            <Slider v-model="imageSliderModel" :min="imageSizeMinGiB" :max="imageSizeMaxGiB" :step="1" />
          </div>
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="isRunning" class="flex flex-col gap-4">
          <div class="flex flex-col gap-2">
            <div class="flex items-center justify-between">
              <p class="text-sm text-ink-gray-7">{{ currentStep }}</p>
              <p class="text-sm text-ink-gray-4">{{ progress }}%</p>
            </div>
            <Progress :value="progress" size="md" />
          </div>
          <button
            type="button"
            class="flex items-center gap-1 self-start text-sm text-ink-gray-5 hover:text-ink-gray-7"
            @click="toggleDetails"
          >
            <FeatherIcon :name="showDetails ? 'chevron-down' : 'chevron-right'" class="h-4 w-4" />
            {{ showDetails ? 'Hide details' : 'Show details' }}
          </button>
          <TerminalOutput v-if="showDetails" ref="terminal" :lines="taskLines" :streaming="taskStreaming" />
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
      <div v-if="(!isRunning && step !== 'done') || (isRunning && error)" class="flex gap-2 border-t border-outline-gray-2 px-5 py-4">
        <Button v-if="isRunning && error" variant="subtle" class="w-full" @click="backToConfig">
          Back to configuration
        </Button>
        <template v-else>
          <Button v-if="stepNumber > 1" variant="subtle" class="flex-1" @click="prevStep">
            Back
          </Button>
          <Button v-if="step === 'passwords'" variant="solid" class="w-full" @click="nextStep">
            Next
          </Button>
          <Button v-else-if="step === 'database'" variant="solid" :loading="loading" class="flex-1" @click="nextStep">
            Next
          </Button>
          <Button v-else-if="!isLastConfigStep" variant="solid" class="flex-1" @click="nextStep">
            Next
          </Button>
          <Button v-else variant="solid" :loading="loading" class="flex-1" @click="initialize">
            Set up bench
          </Button>
        </template>
      </div>
    </div>
  </div>
</template>
