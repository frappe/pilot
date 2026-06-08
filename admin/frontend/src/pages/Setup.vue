<script setup>
import { ref, computed, onMounted } from 'vue'
import { Button, FormControl, FormLabel, Password, Switch, ErrorMessage } from 'frappe-ui'
import AdvancedSetup from '../components/AdvancedSetup.vue'
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

const form = ref({
  python: '3.14',
  sudo_password: '',
  mariadb_password: '',
  admin_password: '',
  app_repo: 'https://github.com/frappe/frappe',
  app_branch: 'version-16',
  http_port: 8000,
  socketio_port: 9000,
  redis_port: 13000,
  workers_default: 2,
  workers_short: 1,
  workers_long: 1,
  volume_enabled: false,
  volume_pool: '',
  volume_device: '',
  volume_benches_reservation: '10G',
  volume_benches_quota: '50G',
  volume_mariadb_reservation: '5G',
  volume_mariadb_quota: '20G',
  volume_mariadb_data_dir: '/var/lib/mysql',
  volume_snapshots_enabled: false,
})

const siteForm = ref({ name: 'site1.localhost', admin_password: 'admin' })

const configSteps = ['passwords', 'customize', 'volume']
const stepNumber = computed(() => configSteps.indexOf(step.value) + 1)
const isConfiguring = computed(() => stepNumber.value > 0)
const isTerminal = computed(() => step.value === 'running' || step.value === 'site-running')
const isWide = computed(() => isTerminal.value)

const titles = {
  passwords: 'Set up passwords',
  customize: 'Customize your bench',
  volume: 'ZFS volume management',
  running: 'Initializing bench…',
  'create-site': 'Create your first site',
  'site-running': 'Creating site…',
}
const subtitles = {
  volume: 'Optional — leave disabled to skip',
}
const title = computed(() => titles[step.value] || benchName.value)
const subtitle = computed(() => subtitles[step.value] || null)

onMounted(loadConfig)

async function loadConfig() {
  try {
    const res = await fetch('/api/setup/config')
    const data = await res.json()
    benchName.value = data.bench_name || ''
    for (const key of Object.keys(form.value)) {
      if (data[key] !== undefined) form.value[key] = data[key]
    }
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

function nextStep() {
  if (step.value === 'passwords' && (!form.value.mariadb_password || !form.value.admin_password)) {
    error.value = 'MariaDB and admin passwords are required.'
    return
  }
  error.value = ''
  step.value = configSteps[configSteps.indexOf(step.value) + 1]
}

function prevStep() {
  error.value = ''
  step.value = configSteps[configSteps.indexOf(step.value) - 1]
}

async function saveConfig() {
  const data = await postJson('/api/setup/save', form.value)
  if (!data.ok) throw new Error(data.error || 'Failed to save configuration.')
}

async function startInitTask() {
  const data = await postJson('/api/setup/init', { sudo_password: form.value.sudo_password })
  if (!data.ok) throw new Error(data.error || 'Failed to start initialization.')
  return data.task_id
}

async function startSiteTask() {
  const data = await postJson('/api/setup/new-site', {
    name: siteForm.value.name,
    admin_password: siteForm.value.admin_password,
  })
  if (!data.ok) throw new Error(data.error || 'Failed to create site.')
  return data.task_id
}

async function initialize() {
  error.value = ''
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
  step.value = 'create-site'
}

async function createSite() {
  if (!siteForm.value.name) {
    error.value = 'Site name is required.'
    return
  }
  error.value = ''
  loading.value = true
  try {
    const taskId = await startSiteTask()
    step.value = 'site-running'
    streamTask(`/api/setup/stream/${taskId}`, onSiteDone)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function onSiteDone(success) {
  if (!success) {
    error.value = 'Site creation failed. Check the output above.'
    return
  }
  emit('done')
}

function backToConfig() {
  error.value = ''
  step.value = step.value === 'site-running' ? 'create-site' : 'volume'
}
</script>

<template>
  <div class="flex h-screen items-center justify-center bg-surface-gray-2 p-4">
    <div
      class="flex w-full flex-col rounded-xl border border-outline-gray-2 bg-surface-white shadow-sm"
      :class="isWide ? 'max-w-2xl' : 'max-w-sm'"
      style="max-height: calc(100vh - 2rem)"
    >
      <!-- Header -->
      <div class="border-b border-outline-gray-2 px-5 py-4">
        <p v-if="isConfiguring" class="mb-1 text-xs text-ink-gray-4">
          Step {{ stepNumber }} of {{ configSteps.length }}
        </p>
        <h1 class="text-base font-medium text-ink-gray-7">{{ title }}</h1>
        <p v-if="subtitle" class="mt-0.5 text-sm text-ink-gray-4">{{ subtitle }}</p>
      </div>

      <!-- Body -->
      <div class="flex-1 overflow-y-auto p-5">
        <div v-if="step === 'passwords'" class="flex flex-col gap-4">
          <div class="space-y-1.5">
            <FormLabel label="Sudo password" />
            <Password v-model="form.sudo_password" placeholder="Used once to install system packages" />
          </div>
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
          <FormControl label="Python version" v-model="form.python" placeholder="3.14" />
          <FormControl label="Frappe branch" v-model="form.app_branch" placeholder="version-16" />
          <AdvancedSetup v-model="form" />
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="step === 'volume'" class="flex flex-col gap-4">
          <Switch
            v-model="form.volume_enabled"
            label="Enable ZFS volume management"
            description="Isolates bench and MariaDB data in ZFS datasets with quotas. Pool and device cannot be changed after initialization."
          />
          <template v-if="form.volume_enabled">
            <div class="grid grid-cols-2 gap-2">
              <FormControl label="Pool name" v-model="form.volume_pool" placeholder="bench-pool" />
              <FormControl label="Block device" v-model="form.volume_device" placeholder="/dev/sdb" />
            </div>
            <div class="grid grid-cols-2 gap-2">
              <FormControl label="Bench reservation" v-model="form.volume_benches_reservation" />
              <FormControl label="Bench quota" v-model="form.volume_benches_quota" />
            </div>
            <div class="grid grid-cols-2 gap-2">
              <FormControl label="MariaDB reservation" v-model="form.volume_mariadb_reservation" />
              <FormControl label="MariaDB quota" v-model="form.volume_mariadb_quota" />
            </div>
            <FormControl label="MariaDB data directory" v-model="form.volume_mariadb_data_dir" />
            <Switch v-model="form.volume_snapshots_enabled" label="Enable snapshots" />
          </template>
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="isTerminal" class="flex flex-col gap-3">
          <TerminalOutput ref="terminal" :lines="taskLines" :streaming="taskStreaming" />
          <ErrorMessage v-if="error" :message="error" />
        </div>

        <div v-else-if="step === 'create-site'" class="flex flex-col gap-4">
          <FormControl label="Site name" v-model="siteForm.name" placeholder="site1.localhost" />
          <div class="space-y-1.5">
            <FormLabel label="Site admin password" />
            <Password v-model="siteForm.admin_password" placeholder="admin" @keydown.enter="createSite" />
          </div>
          <ErrorMessage v-if="error" :message="error" />
        </div>
      </div>

      <!-- Footer -->
      <div v-if="!isTerminal || error" class="flex gap-2 border-t border-outline-gray-2 px-5 py-4">
        <Button v-if="step === 'customize' || step === 'volume'" variant="subtle" class="flex-1" @click="prevStep">
          Back
        </Button>
        <Button v-if="isTerminal && error" variant="subtle" class="w-full" @click="backToConfig">
          Back to configuration
        </Button>
        <Button v-else-if="step === 'passwords'" variant="solid" class="w-full" @click="nextStep">
          Next
        </Button>
        <Button v-else-if="step === 'customize'" variant="solid" class="flex-1" @click="nextStep">
          Next
        </Button>
        <Button v-else-if="step === 'volume'" variant="solid" :loading="loading" class="flex-1" @click="initialize">
          Initialize
        </Button>
        <template v-else-if="step === 'create-site'">
          <Button variant="subtle" class="flex-1" @click="$emit('done')">
            Go to Dashboard
          </Button>
          <Button variant="solid" :loading="loading" class="flex-1" @click="createSite">
            Create Site
          </Button>
        </template>
      </div>
    </div>
  </div>
</template>
