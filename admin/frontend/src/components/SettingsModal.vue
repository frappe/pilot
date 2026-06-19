<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, FormControl, ErrorMessage, LoadingText, Select, Badge, useTheme, Dialog } from 'frappe-ui'
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

const BASE_TABS = [
  { key: 'bench', label: 'Bench' },
  { key: 'appearance', label: 'Appearance' },
  { key: 'mariadb', label: 'MariaDB' },
  { key: 'redis', label: 'Redis' },
  { key: 'workers', label: 'Workers' },
  { key: 'updates', label: 'Updates' },
]
const isLinux = ref(false)
const TABS = computed(() => {
  let tabs = isLinux.value
    ? [...BASE_TABS, { key: 'volume', label: 'ZFS Volume' }]
    : BASE_TABS

    return tabs
}
)

const activeTab = ref('bench')

const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')
const saveSuccess = ref('')

const PROCESS_MANAGER_OPTIONS = [
  { label: 'None (development)', value: 'none' },
  { label: 'Supervisor', value: 'supervisor' },
  { label: 'Systemd', value: 'systemd' },
]

const form = ref(null)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch('/api/settings/')
    if (!res.ok) throw new Error(`${res.status}`)
    const data = await res.json()
    isLinux.value = data.is_linux === true
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
    if (!d.ok) { saveError.value = d.error; return }

    const pm = form.value.production.process_manager
    const setupCommand = pm !== 'none' ? 'setup-production' : null

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
      <div class="flex h-[calc(100vh-8rem)] bg-surface-menu-bar" @pointerdown.stop>
        <!-- Left sidebar - full height, same bg as outer, no border -->
        <div class="flex flex-col m-1 w-48 shrink-0 rounded-l-lg bg-surface-menu-bar overflow-y-auto">
          <h3 class="px-3 py-3 font-semibold text-ink-gray-9 sticky top-0 bg-surface-menu-bar">
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
            <h3 class="font-semibold text-ink-gray-9">
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

            <template v-else-if="form">
              <!-- Bench -->
              <div v-if="activeTab === 'bench'" class="flex flex-col gap-4">
                <h4 class="font-semibold text-ink-gray-8">Process Manager</h4>
                <Select :options="PROCESS_MANAGER_OPTIONS" v-model="form.production.process_manager" class="w-64" />
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
                <p class="rounded-md bg-surface-gray-2 px-3 py-2 text-xs text-ink-gray-5">
                  MariaDB connection settings are set during bench initialization and cannot be changed here.
                </p>
                <div class="grid grid-cols-2 gap-4">
                  <FormControl label="Host" :modelValue="form.mariadb.host" disabled />
                  <FormControl type="number" label="Port" :modelValue="form.mariadb.port" disabled />
                  <FormControl label="Admin User" :modelValue="form.mariadb.admin_user" disabled />
                  <FormControl label="Version" :modelValue="form.mariadb.version" disabled />
                  <FormControl class="col-span-2" label="Socket Path" :modelValue="form.mariadb.socket_path" disabled />
                </div>
              </div>

              <!-- Redis -->
              <div v-else-if="activeTab === 'redis'" class="flex flex-col gap-4">
                <div class="grid grid-cols-2 gap-4">
                  <FormControl type="number" label="Cache Port" v-model="form.redis.cache_port" />
                  <FormControl type="number" label="Queue Port" v-model="form.redis.queue_port" />
                  <FormControl label="Version" v-model="form.redis.version" disabled placeholder="not installed" />
                </div>
              </div>

              <!-- Workers -->
              <div v-else-if="activeTab === 'workers'" class="flex flex-col gap-4">
                <p class="text-sm text-ink-gray-6">
                  Each group spawns <span class="font-medium">count</span> workers listening to the listed queues.
                </p>
                <div
                  v-for="(group, i) in form.workers"
                  :key="i"
                  class="grid grid-cols-[1fr_7rem_auto] items-end gap-3"
                >
                  <FormControl
                    :label="i === 0 ? 'Queues' : undefined"
                    v-model="group.queues"
                    placeholder="default, short, long"
                  />
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

              <!-- ZFS Volume -->
              <div v-else-if="activeTab === 'volume'" class="flex flex-col gap-4">
                <div class="grid grid-cols-2 gap-4">
                  <FormControl label="Pool Name" :modelValue="form.volume.pool" disabled />
                  <FormControl
                    v-if="form.volume.backing === 'image'"
                    label="Disk Image"
                    :modelValue="`${form.volume.image_path} (${form.volume.image_size})`"
                    disabled
                  />
                  <FormControl v-else label="Block Device" :modelValue="form.volume.device" disabled />
                </div>
                <div class="grid grid-cols-2 gap-4">
                  <FormControl label="Reservation" v-model="form.volume.reservation" />
                  <FormControl label="Quota" v-model="form.volume.quota" />
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
                  <div v-if="showUpdateDetails" class="rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-4 py-3 flex flex-col gap-2.5 text-sm">
                    <div class="flex items-baseline gap-3">
                      <span class="w-14 shrink-0 text-ink-gray-5">Current</span>
                      <span class="font-mono text-xs font-semibold text-ink-gray-8 break-all">{{ cliUpdate.local_commit }}</span>
                    </div>
                    <div class="flex items-baseline gap-3">
                      <span class="w-14 shrink-0 text-ink-gray-5">Latest</span>
                      <span class="font-mono text-xs font-semibold text-ink-gray-8 break-all">{{ cliUpdate.remote_commit || '—' }}</span>
                    </div>
                  </div>
                </template>
              </div>

            </template>
          </div>

          <!-- Footer -->
          <div v-if="activeTab !== 'appearance' && activeTab !== 'updates'" class="flex items-center justify-end gap-3 px-6 py-3 border-t border-outline-gray-1 flex-shrink-0">
            <ErrorMessage :message="saveError" />
            <span v-if="saveSuccess" class="text-sm text-ink-green-2 font-medium">{{ saveSuccess }}</span>
            <Button @click="show = false">Cancel</Button>
            <Button variant="solid" :loading="saving" @click="save">Save</Button>
          </div>
        </div>
      </div>
    </template>
  </Dialog>
</template>
