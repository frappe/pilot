<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, FormControl, ErrorMessage, LoadingText, Switch, Select, Badge, useTheme, Dialog } from 'frappe-ui'
import LucideX from '~icons/lucide/x'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue'])
const router = useRouter()

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const { currentTheme, setTheme } = useTheme()
const theme = computed({
  get: () => currentTheme.value,
  set: (val) => setTheme(val),
})
const THEME_OPTIONS = [
  { label: 'Light', value: 'light' },
  { label: 'Dark', value: 'dark' },
  { label: 'Auto (System)', value: 'system' },
]

const TABS = [
  { key: 'bench', label: 'Bench' },
  { key: 'appearance', label: 'Appearance' },
  { key: 'mariadb', label: 'MariaDB' },
  { key: 'redis', label: 'Redis' },
  { key: 'workers', label: 'Workers' },
  { key: 'nginx', label: 'Nginx' },
  { key: 'letsencrypt', label: "Let's Encrypt" },
  { key: 'updates', label: 'Updates' },
]
const activeTab = ref('bench')

const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')
const saveSuccess = ref('')

const PROCESS_MANAGER_OPTIONS = [
  { label: 'None (development)', value: 'none' },
  { label: 'Supervisor', value: 'supervisor' },
  { label: 'Systemd', value: 'systemd' },
]

const form = ref({
  bench: { name: '', python: '', http_port: 8000, socketio_port: 9000 },
  mariadb: { host: 'localhost', port: 3306, admin_user: 'root', socket_path: '', version: '' },
  redis: { cache_port: 13000, queue_port: 11000, socketio_port: 12000, version: '' },
  workers: { default: 2, short: 1, long: 1 },
  nginx: { http_port: 80, https_port: 443, config_dir: '/etc/nginx/conf.d', worker_processes: 'auto', client_max_body_size: '50m' },
  letsencrypt: { email: '', webroot_path: '/var/www/letsencrypt' },
  production: { process_manager: 'none', nginx: false },
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch('/api/settings/')
    if (!res.ok) throw new Error(`${res.status}`)
    form.value = await res.json()
  } catch (e) {
    loadError.value = e.message
  } finally {
    loading.value = false
  }
}

function validateSettings() {
  const ports = [
    [form.value.bench.http_port, 'HTTP Port'],
    [form.value.bench.socketio_port, 'SocketIO Port'],
    [form.value.mariadb.port, 'MariaDB Port'],
    [form.value.redis.cache_port, 'Redis Cache Port'],
    [form.value.redis.queue_port, 'Redis Queue Port'],
    [form.value.redis.socketio_port, 'Redis SocketIO Port'],
    [form.value.nginx.http_port, 'Nginx HTTP Port'],
    [form.value.nginx.https_port, 'Nginx HTTPS Port'],
  ]
  for (const [port, name] of ports) {
    const n = Number(port)
    if (!Number.isInteger(n) || n < 1 || n > 65535)
      return `${name} must be between 1 and 65535.`
  }
  for (const [key, label] of [['default', 'Default'], ['short', 'Short'], ['long', 'Long']]) {
    const n = Number(form.value.workers[key])
    if (!Number.isInteger(n) || n < 1)
      return `${label} workers must be at least 1.`
  }
  const email = (form.value.letsencrypt.email || '').trim()
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
    return "Invalid email address for Let's Encrypt."
  return null
}

async function save() {
  saveError.value = ''
  saveSuccess.value = ''
  const validationError = validateSettings()
  if (validationError) { saveError.value = validationError; return }
  saving.value = true
  try {
    const res = await fetch('/api/settings/', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value),
    })
    const d = await res.json()
    if (!d.ok) { saveError.value = d.error; return }

    const pm = form.value.production.process_manager
    const nginxEnabled = form.value.production.nginx
    const setupCommand = pm !== 'none' ? 'setup-production' : nginxEnabled ? 'setup-nginx' : null

    if (setupCommand) {
      const taskRes = await fetch('/api/tasks/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: setupCommand }),
      })
      const taskData = await taskRes.json()
      if (taskData.ok) {
        show.value = false
        router.push(`/tasks/${taskData.task_id}`)
        return
      }
      saveError.value = taskData.error || 'Setup failed to start'
      return
    }

    saveSuccess.value = d.restarted ? 'Saved & restarted' : 'Saved'
    setTimeout(() => { saveSuccess.value = '' }, 3000)
    if (d.restart_error) saveError.value = `Restart failed: ${d.restart_error}`
  } catch (e) {
    saveError.value = e.message
  } finally {
    saving.value = false
  }
}

const cliUpdate = ref(null)
const checkingUpdate = ref(false)
const checkUpdateError = ref('')
const showUpdateDetails = ref(false)

async function checkCliUpdate() {
  checkingUpdate.value = true
  checkUpdateError.value = ''
  try {
    const res = await fetch('/api/updates/cli?fetch=1')
    if (!res.ok) throw new Error(`${res.status}`)
    cliUpdate.value = await res.json()
  } catch (e) {
    checkUpdateError.value = e.message
  } finally {
    checkingUpdate.value = false
  }
}

async function updateCli() {
  try {
    const res = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'update-cli' }),
    })
    const d = await res.json()
    if (d.ok) {
      show.value = false
      router.push(`/tasks/${d.task_id}`)
    } else {
      checkUpdateError.value = d.error
    }
  } catch (e) {
    checkUpdateError.value = e.message
  }
}

watch(() => props.modelValue, (val) => {
  if (val) {
    activeTab.value = 'bench'
    saveError.value = ''
    saveSuccess.value = ''
    cliUpdate.value = null
    showUpdateDetails.value = false
    load()
  }
})
</script>

<template>
  <Dialog v-model="show" :options="{ size: '3xl' }">
    <template #body>
      <div class="flex h-[calc(100vh-8rem)] bg-surface-menu-bar">
        <!-- Left sidebar - full height, same bg as outer, no border -->
        <div class="flex flex-col m-1 w-48 shrink-0 rounded-l-lg bg-surface-menu-bar overflow-y-auto">
          <h3 class="px-3 py-3 text-sm font-semibold text-ink-gray-9 sticky top-0 bg-surface-menu-bar">
            Settings
          </h3>
          <nav class="space-y-0.5 px-1">
            <button
              v-for="tab in TABS"
              :key="tab.key"
              @click="activeTab = tab.key"
              class="flex h-7.5 w-full cursor-pointer items-center rounded px-2 py-[7px] text-sm text-ink-gray-8 duration-300 ease-in-out focus:outline-none"
              :class="activeTab === tab.key
                ? 'bg-surface-selected shadow-sm'
                : 'hover:bg-surface-gray-2'"
            >
              {{ tab.label }}
            </button>
          </nav>
        </div>

        <!-- Right content area -->
        <div class="flex flex-col flex-1 overflow-hidden bg-surface-modal rounded-r-xl">
          <!-- Header with close button -->
          <div class="flex items-center justify-between px-6 py-4 border-b border-outline-gray-1 flex-shrink-0">
            <h3 class="text-base font-semibold text-ink-gray-9">
              {{ TABS.find(t => t.key === activeTab)?.label }}
            </h3>
            <Button variant="ghost" @click="show = false">
              <template #icon>
                <LucideX class="h-4 w-4 text-ink-gray-7" />
              </template>
            </Button>
          </div>

          <!-- Scrollable content -->
          <div class="flex-1 overflow-y-auto px-6 py-5">
            <LoadingText v-if="loading" />
            <ErrorMessage v-else-if="loadError" :message="loadError" />

            <template v-else>
              <!-- Bench -->
              <div v-if="activeTab === 'bench'" class="flex flex-col gap-4">
                <h4 class="text-sm font-semibold text-ink-gray-8">Process Manager</h4>
                <Select label="Process Manager" :options="PROCESS_MANAGER_OPTIONS" v-model="form.production.process_manager" class="w-64" />
                <div class="border-t border-outline-gray-1" />
                <div class="grid grid-cols-2 gap-4">
                  <FormControl label="Name" :modelValue="form.bench.name" disabled />
                  <FormControl label="Python Version" :modelValue="form.bench.python" disabled />
                  <FormControl type="number" label="HTTP Port" v-model="form.bench.http_port" />
                  <FormControl type="number" label="SocketIO Port" v-model="form.bench.socketio_port" />
                </div>
              </div>

              <!-- Appearance -->
              <div v-else-if="activeTab === 'appearance'" class="flex flex-col gap-4">
                <Select label="Theme" :options="THEME_OPTIONS" v-model="theme" class="w-48" />
              </div>

              <!-- MariaDB -->
              <div v-else-if="activeTab === 'mariadb'" class="flex flex-col gap-4">
                <div class="grid grid-cols-2 gap-4">
                  <FormControl label="Host" v-model="form.mariadb.host" />
                  <FormControl type="number" label="Port" v-model="form.mariadb.port" />
                  <FormControl label="Admin User" v-model="form.mariadb.admin_user" />
                  <FormControl label="Version" v-model="form.mariadb.version" placeholder="e.g. 10.6" />
                  <FormControl class="col-span-2" label="Socket Path" v-model="form.mariadb.socket_path" placeholder="Leave empty to use TCP" />
                </div>
              </div>

              <!-- Redis -->
              <div v-else-if="activeTab === 'redis'" class="flex flex-col gap-4">
                <div class="grid grid-cols-2 gap-4">
                  <FormControl type="number" label="Cache Port" v-model="form.redis.cache_port" />
                  <FormControl type="number" label="Queue Port" v-model="form.redis.queue_port" />
                  <FormControl type="number" label="SocketIO Port" v-model="form.redis.socketio_port" />
                  <FormControl label="Version" v-model="form.redis.version" placeholder="e.g. 7" />
                </div>
              </div>

              <!-- Workers -->
              <div v-else-if="activeTab === 'workers'" class="flex flex-col gap-4">
                <div class="grid grid-cols-3 gap-4">
                  <FormControl type="number" label="Default Workers" v-model="form.workers.default" />
                  <FormControl type="number" label="Short Workers" v-model="form.workers.short" />
                  <FormControl type="number" label="Long Workers" v-model="form.workers.long" />
                </div>
              </div>

              <!-- Nginx -->
              <div v-else-if="activeTab === 'nginx'" class="flex flex-col gap-4">
                <Switch v-model="form.production.nginx" label="Manage Nginx" />
                <div class="grid grid-cols-2 gap-4">
                  <FormControl type="number" label="HTTP Port" v-model="form.nginx.http_port" />
                  <FormControl type="number" label="HTTPS Port" v-model="form.nginx.https_port" />
                  <FormControl label="Worker Processes" v-model="form.nginx.worker_processes" placeholder="auto" />
                  <FormControl label="Client Max Body Size" v-model="form.nginx.client_max_body_size" placeholder="50m" />
                  <FormControl class="col-span-2" label="Config Directory" v-model="form.nginx.config_dir" />
                </div>
              </div>

              <!-- Let's Encrypt -->
              <div v-else-if="activeTab === 'letsencrypt'" class="flex flex-col gap-4">
                <div class="grid grid-cols-2 gap-4">
                  <FormControl label="Email" v-model="form.letsencrypt.email" placeholder="you@example.com" />
                  <FormControl label="Webroot Path" v-model="form.letsencrypt.webroot_path" />
                </div>
              </div>

              <!-- Updates -->
              <div v-else-if="activeTab === 'updates'" class="flex flex-col gap-4">
                <Button variant="outline" :loading="checkingUpdate" @click="checkCliUpdate">Check for Updates</Button>
                <ErrorMessage :message="checkUpdateError" />
                <template v-if="cliUpdate">
                  <div class="flex items-center gap-3">
                    <Badge v-if="cliUpdate.update_available" label="Update available" theme="orange" />
                    <Badge v-else label="Up to date" theme="green" />
                    <Button v-if="cliUpdate.update_available" variant="solid" @click="updateCli">Update</Button>
                    <button class="ml-auto text-xs text-ink-gray-4 hover:text-ink-gray-7" @click="showUpdateDetails = !showUpdateDetails">
                      {{ showUpdateDetails ? 'Hide details' : 'Details' }}
                    </button>
                  </div>
                  <div v-if="showUpdateDetails" class="flex flex-col gap-1.5 text-sm">
                    <div class="flex items-center gap-2">
                      <span class="w-16 text-ink-gray-5">Current</span>
                      <span class="font-mono text-ink-gray-8">{{ cliUpdate.local_commit }}</span>
                    </div>
                    <div class="flex items-center gap-2">
                      <span class="w-16 text-ink-gray-5">Latest</span>
                      <span class="font-mono text-ink-gray-8">{{ cliUpdate.remote_commit || '—' }}</span>
                    </div>
                  </div>
                </template>
              </div>

            </template>
          </div>

          <!-- Footer -->
          <div v-if="activeTab !== 'appearance' && activeTab !== 'updates'" class="flex items-center justify-end gap-3 px-6 py-3 border-t border-outline-gray-1 flex-shrink-0">
            <ErrorMessage :message="saveError" />
            <span v-if="saveSuccess" class="text-sm text-green-600 font-medium">{{ saveSuccess }}</span>
            <Button @click="show = false">Cancel</Button>
            <Button variant="solid" :loading="saving" @click="save">Save</Button>
          </div>
        </div>
      </div>
    </template>
  </Dialog>
</template>
