<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Button, FormControl, ErrorMessage, LoadingText, Switch, Select, useTheme } from 'frappe-ui'
import { useTaskProgress } from '../composables/useTaskProgress.js'

const router = useRouter()
const { watchTask } = useTaskProgress()

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
  redis: { cache_port: 13000, queue_port: 11000, version: '' },
  workers: [{ queues: 'default, short, long', count: 1 }],
  production: { enabled: false, lightweight: false },
  admin: { domain: '', tls: false },
  letsencrypt: { email: '' },
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch('/api/settings/')
    if (!res.ok) throw new Error(`${res.status}`)
    const data = await res.json()
    if (Array.isArray(data.workers))
      data.workers = data.workers.map(g => ({ queues: (g.queues || []).join(', '), count: g.count }))
    form.value = data
  } catch (e) {
    loadError.value = e.message
  } finally {
    loading.value = false
  }
}

function queueList(q) {
  if (Array.isArray(q)) return q.map(s => String(s).trim()).filter(Boolean)
  return String(q || '').split(',').map(s => s.trim()).filter(Boolean)
}

function addWorkerGroup() {
  form.value.workers.push({ queues: '', count: 1 })
}

function removeWorkerGroup(i) {
  form.value.workers.splice(i, 1)
}

function validateSettings() {
  const ports = [
    [form.value.bench.http_port, 'HTTP Port'],
    [form.value.bench.socketio_port, 'SocketIO Port'],
    [form.value.mariadb.port, 'MariaDB Port'],
    [form.value.redis.cache_port, 'Redis Cache Port'],
    [form.value.redis.queue_port, 'Redis Queue Port'],
  ]
  for (const [port, name] of ports) {
    const n = Number(port)
    if (!Number.isInteger(n) || n < 1 || n > 65535)
      return `${name} must be between 1 and 65535.`
  }
  if (!Array.isArray(form.value.workers) || form.value.workers.length === 0)
    return 'Add at least one worker group.'
  for (const [i, group] of form.value.workers.entries()) {
    if (!queueList(group.queues).length)
      return `Worker group ${i + 1} needs at least one queue.`
    const n = Number(group.count)
    if (!Number.isInteger(n) || n < 1)
      return `Worker group ${i + 1} count must be at least 1.`
  }
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
const httpsApplying = ref(false)

// Enabling HTTPS is a two-step action: persist the choice, then run the task
// that actually obtains certificates (or, when disabling, regenerates plain
// HTTP routing). Both stream their progress on the task page we route to.
async function applyHttps() {
  saveError.value = ''
  saveSuccess.value = ''
  if (form.value.admin.tls && !String(form.value.letsencrypt.email || '').trim()) {
    saveError.value = "An email is required to issue Let's Encrypt certificates."
    return
  }
  httpsApplying.value = true
  try {
    const res = await fetch('/api/settings/', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        admin: { tls: form.value.admin.tls },
        letsencrypt: { email: form.value.letsencrypt.email },
      }),
    })
    const d = await res.json()
    if (!d.ok) { saveError.value = d.error; return }
    await runTask(form.value.admin.tls ? 'setup-letsencrypt' : 'setup-nginx')
  } catch (e) {
    saveError.value = e.message
  } finally {
    httpsApplying.value = false
  }
}

async function runTask(command) {
  taskError.value = ''
  try {
    const res = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    })
    const d = await res.json()
    if (d.ok) watchTask(d.task_id)
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
    <Teleport defer to="#header-actions">
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

      <!-- HTTPS -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">HTTPS</h3>
        <p class="text-sm text-ink-gray-6">
          The bench is served over plain HTTP by default. Enable HTTPS to obtain a
          Let's Encrypt certificate for the admin and SSL sites; HTTP is then
          redirected to HTTPS. Leave it off when a proxy in front terminates TLS.
        </p>
        <Switch v-model="form.admin.tls" label="Enable HTTPS (Let's Encrypt)" />
        <FormControl
          v-if="form.admin.tls"
          type="email"
          label="Let's Encrypt email"
          v-model="form.letsencrypt.email"
          placeholder="you@example.com"
        />
        <div>
          <Button variant="outline" :loading="httpsApplying" @click="applyHttps">
            {{ form.admin.tls ? 'Enable HTTPS & issue certificate' : 'Disable HTTPS' }}
          </Button>
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Bench -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Bench</h3>
        <div class="grid grid-cols-2 gap-4">
          <FormControl label="Name" :modelValue="form.bench.name" disabled />
          <FormControl label="Python Version" :modelValue="form.bench.python" disabled />
          <FormControl label="Default Branch" v-model="form.bench.default_branch" placeholder="develop" />
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
          <FormControl label="Version" v-model="form.redis.version" disabled placeholder="not installed" />
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Workers -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Workers</h3>
        <p class="text-sm text-ink-gray-6">
          Each group spawns <span class="font-medium">count</span> workers listening to the listed queues.
        </p>
        <div
          v-for="(group, i) in form.workers"
          :key="i"
          class="grid grid-cols-[1fr_7rem_auto] items-end gap-3"
        >
          <FormControl :label="i === 0 ? 'Queues' : undefined" v-model="group.queues" placeholder="default, short, long" />
          <FormControl type="number" :min="1" :label="i === 0 ? 'Count' : undefined" v-model.number="group.count" />
          <Button
            variant="ghost"
            icon="trash-2"
            :disabled="form.workers.length === 1"
            @click="removeWorkerGroup(i)"
          />
        </div>
        <div>
          <Button variant="subtle" icon-left="plus" label="Add group" @click="addWorkerGroup" />
        </div>
      </div>

      <div class="border-t border-outline-gray-1" />

      <!-- Setup -->
      <div class="flex flex-col gap-4">
        <h3 class="font-semibold text-ink-gray-8">Setup</h3>
        <ErrorMessage :message="taskError" />
        <div class="flex flex-wrap gap-2">
          <Button variant="outline" :loading="taskLoading === 'setup-nginx'" @click="taskLoading = 'setup-nginx'; runTask('setup-nginx')">Refresh Web Routing</Button>
          <Button variant="outline" :loading="taskLoading === 'setup-production'" @click="taskLoading = 'setup-production'; runTask('setup-production')">Setup Production</Button>
          <Button variant="outline" :loading="taskLoading === 'update'" @click="taskLoading = 'update'; runTask('update')">Update Bench</Button>
        </div>
      </div>
    </template>
  </div>
</template>
