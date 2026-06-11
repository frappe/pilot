<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Button, FormControl, ErrorMessage, LoadingText, Switch, Select, useTheme } from 'frappe-ui'

const router = useRouter()

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

const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')
const saveSuccess = ref('')

const form = ref({
  bench: { name: '', python: '', http_port: 8000, socketio_port: 9000, default_branch: '' },
  mariadb: { host: 'localhost', port: 3306, admin_user: 'root', socket_path: '', version: '' },
  redis: { cache_port: 13000, queue_port: 11000, socketio_port: 12000, version: '' },
  workers: { default: 2, short: 1, long: 1 },
  nginx: { http_port: 80, https_port: 443, config_dir: '/etc/nginx/conf.d', worker_processes: 'auto', client_max_body_size: '50m' },
  letsencrypt: { email: '', webroot_path: '/var/www/letsencrypt' },
  production: { enabled: false, nginx: false, lightweight: false },
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
    if (d.ok) {
      saveSuccess.value = d.restarted ? 'Saved & restarted' : 'Saved'
      setTimeout(() => { saveSuccess.value = '' }, 3000)
      if (d.restart_error) saveError.value = `Restart failed: ${d.restart_error}`
    } else {
      saveError.value = d.error
    }
  } catch (e) {
    saveError.value = e.message
  } finally {
    saving.value = false
  }
}

const taskLoading = ref('')
const taskError = ref('')

async function runTask(command) {
  taskError.value = ''
  try {
    const res = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    })
    const d = await res.json()
    if (d.ok) router.push(`/tasks/${d.task_id}`)
    else taskError.value = d.error
  } catch (e) {
    taskError.value = e.message
  } finally {
    taskLoading.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="max-w-2xl mx-auto flex flex-col gap-6">
    <Teleport to="#header-actions">
      <span v-if="saveSuccess" class="text-sm text-ink-green-2 font-medium">{{ saveSuccess }}</span>
      <Button variant="solid" :loading="saving" @click="save">Save</Button>
    </Teleport>
    <ErrorMessage :message="saveError" />

    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="loadError" :message="loadError" />

    <template v-else>
      <!-- Appearance -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Appearance</h3>
        <Select label="Theme" :options="THEME_OPTIONS" v-model="theme" class="w-40" />
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Mode -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Mode</h3>
        <div class="flex flex-col gap-3">
          <Switch v-model="form.production.enabled" label="Production Mode" />
          <div v-if="form.production.enabled" class="flex flex-col gap-3 pl-4 border-l border-outline-gray-2">
            <Switch v-model="form.production.lightweight" label="Lightweight" />
          </div>
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Bench -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Bench</h3>
        <div class="grid grid-cols-2 gap-4">
          <FormControl label="Name" :modelValue="form.bench.name" disabled />
          <FormControl label="Python Version" :modelValue="form.bench.python" disabled />
          <FormControl label="Default Branch" v-model="form.bench.default_branch" placeholder="version-16" />
          <div />
          <FormControl type="number" label="HTTP Port" v-model="form.bench.http_port" />
          <FormControl type="number" label="SocketIO Port" v-model="form.bench.socketio_port" />
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- MariaDB -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">MariaDB</h3>
        <div class="grid grid-cols-2 gap-4">
          <FormControl label="Host" v-model="form.mariadb.host" />
          <FormControl type="number" label="Port" v-model="form.mariadb.port" />
          <FormControl label="Admin User" v-model="form.mariadb.admin_user" />
          <FormControl label="Version" v-model="form.mariadb.version" placeholder="e.g. 10.6" />
          <FormControl class="col-span-2" label="Socket Path" v-model="form.mariadb.socket_path" placeholder="Leave empty to use TCP" />
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Redis -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Redis</h3>
        <div class="grid grid-cols-2 gap-4">
          <FormControl type="number" label="Cache Port" v-model="form.redis.cache_port" />
          <FormControl type="number" label="Queue Port" v-model="form.redis.queue_port" />
          <FormControl type="number" label="SocketIO Port" v-model="form.redis.socketio_port" />
          <FormControl label="Version" v-model="form.redis.version" placeholder="e.g. 7" />
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Workers -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Workers</h3>
        <div class="grid grid-cols-3 gap-4">
          <FormControl type="number" label="Default Workers" v-model="form.workers.default" />
          <FormControl type="number" label="Short Workers" v-model="form.workers.short" />
          <FormControl type="number" label="Long Workers" v-model="form.workers.long" />
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Nginx -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Nginx</h3>
        <Switch v-model="form.production.nginx" label="Manage Nginx" />
        <div class="grid grid-cols-2 gap-4">
          <FormControl type="number" label="HTTP Port" v-model="form.nginx.http_port" />
          <FormControl type="number" label="HTTPS Port" v-model="form.nginx.https_port" />
          <FormControl label="Worker Processes" v-model="form.nginx.worker_processes" placeholder="auto" />
          <FormControl label="Client Max Body Size" v-model="form.nginx.client_max_body_size" placeholder="50m" />
          <FormControl class="col-span-2" label="Config Directory" v-model="form.nginx.config_dir" />
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Let's Encrypt -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Let's Encrypt</h3>
        <div class="grid grid-cols-2 gap-4">
          <FormControl label="Email" v-model="form.letsencrypt.email" placeholder="you@example.com" />
          <FormControl label="Webroot Path" v-model="form.letsencrypt.webroot_path" />
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Setup -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Setup</h3>
        <ErrorMessage :message="taskError" />
        <div class="flex flex-wrap gap-2">
          <Button variant="outline" :loading="taskLoading === 'setup-nginx'" @click="taskLoading = 'setup-nginx'; runTask('setup-nginx')">Setup Nginx</Button>
          <Button variant="outline" :loading="taskLoading === 'setup-production'" @click="taskLoading = 'setup-production'; runTask('setup-production')">Setup Production</Button>
          <Button variant="outline" :loading="taskLoading === 'update'" @click="taskLoading = 'update'; runTask('update')">Update Bench</Button>
        </div>
      </div>
    </template>
  </div>
</template>
