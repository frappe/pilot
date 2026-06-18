<script setup>
import { Button, Dialog, ErrorMessage, FormControl, Sidebar, SidebarItem } from 'frappe-ui'
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import LucideActivity from '~icons/lucide/activity'
import LucideCamera from '~icons/lucide/camera'
import LucideDatabase from '~icons/lucide/database'
import LucideFileText from '~icons/lucide/file-text'
import LucideGlobe from '~icons/lucide/globe'
import LucideListTodo from '~icons/lucide/list-todo'
import LucideLogOut from '~icons/lucide/log-out'
import LucidePackage2 from '~icons/lucide/package-2'
import LucideStore from '~icons/lucide/store'
import LucideSettings from '~icons/lucide/settings'
import LucideRepeat from '~icons/lucide/repeat'
import LucidePlus from '~icons/lucide/plus'
import LucideCheck from '~icons/lucide/check'

const emit = defineEmits(['logout', 'open-settings'])

const route = useRoute()

const benches = ref([])
const showBenchDialog = ref(false)
const currentPort = window.location.port
const benchName = ref('Bench')

async function loadBenches() {
  try {
    const response = await fetch('/api/benches/')
    if (response.ok) {
      benches.value = await response.json()
    }
  } catch { }
}

async function loadBenchName() {
  try {
    const response = await fetch('/api/status')
    if (response.ok) {
      const data = await response.json()
      if (data.name) benchName.value = data.name
    }
  } catch { }
}

// Opened from the sidebar header dropdown (reka-ui Menu). The menu applies
// inert/pointer-events:none to the rest of the page while open and only
// releases it after it finishes closing — if we mount a Dialog in the same
// tick, its subtree inherits that inert state and becomes unclickable. Defer
// the open until the menu has torn down.
function openAfterMenuCloses(fn) {
  nextTick(() => requestAnimationFrame(fn))
}

function openBenchDialog() {
  loadBenches()
  openAfterMenuCloses(() => {
    showBenchDialog.value = true
  })
}

function switchBench(bench) {
  if (!bench.running || String(bench.port) === String(currentPort)) return
  window.location.href = `${window.location.protocol}//${window.location.hostname}:${bench.port}`
}

const showNewBenchDialog = ref(false)
const newBenchName = ref('')
const newBenchError = ref('')
const newBenchCreating = ref(false)
const newBenchStatus = ref('')

function openNewBenchDialog() {
  newBenchName.value = ''
  newBenchError.value = ''
  newBenchCreating.value = false
  newBenchStatus.value = ''
  openAfterMenuCloses(() => {
    showNewBenchDialog.value = true
  })
}

async function waitUntilLive(port, attempt = 0) {
  try {
    const response = await fetch(`/api/benches/ready?port=${port}`)
    if (response.ok) {
      const data = await response.json()
      if (data.ready) {
        newBenchStatus.value = 'Bench created! Redirecting you to setup…'
        window.location.href = `${window.location.protocol}//${window.location.hostname}:${port}`
        return
      }
    }
  } catch { }
  if (attempt >= 60) {
    newBenchError.value = 'New bench admin server did not come up in time.'
    newBenchCreating.value = false
    return
  }
  setTimeout(() => waitUntilLive(port, attempt + 1), 1000)
}

async function createBench() {
  const name = newBenchName.value.trim()
  if (!name) return
  if (!/^[a-zA-Z0-9_-]+$/.test(name)) {
    newBenchError.value = "Bench name must contain only letters, numbers, '-' and '_'"
    return
  }
  newBenchError.value = ''
  newBenchCreating.value = true
  try {
    const response = await fetch('/api/benches/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newBenchName.value.trim() }),
    })
    const data = await response.json()
    if (!response.ok) {
      newBenchError.value = data.error || 'Failed to create bench'
      newBenchCreating.value = false
      return
    }
    newBenchStatus.value = 'Bench created — waiting for it to come up…'
    waitUntilLive(data.port)
  } catch {
    newBenchError.value = 'Failed to create bench'
    newBenchCreating.value = false
  }
}

const header = computed(() => ({
  title: benchName.value,
  logo: '/logos/frappe-icon.png',
  menuItems: [
    { label: 'Settings', icon: LucideSettings, onClick: () => emit('open-settings') },
    { label: 'Change Bench', icon: LucideRepeat, onClick: openBenchDialog },
    { label: 'New Bench', icon: LucidePlus, onClick: openNewBenchDialog },
    { label: 'Logout', icon: LucideLogOut, onClick: () => logout() },
  ],
}))

const baseNavItems = [
  { label: 'Sites', to: '/', icon: LucideGlobe },
  { label: 'Apps', to: '/apps', icon: LucidePackage2 },
  { label: 'Marketplace', to: '/marketplace', icon: LucideStore },
  { label: 'Monitor', to: '/monitor', icon: LucideActivity },
  { label: 'Logs', to: '/logs', icon: LucideFileText },
  { label: 'Database', to: '/database', icon: LucideDatabase },
  { label: 'Tasks', to: '/tasks', icon: LucideListTodo },
]

const snapshotsEnabled = ref(false)
const runningCount = ref(0)
let pollTimer = null

const navItems = computed(() => [
  ...baseNavItems,
  ...(snapshotsEnabled.value ? [{ label: 'Snapshots', to: '/snapshots', icon: LucideCamera }] : []),
])

const sections = computed(() => [{ items: navItems.value }])

function isActive(to) {
  if (to === '/') return route.path === '/' || route.path.startsWith('/sites')
  return route.path.startsWith(to)
}

async function pollRunning() {
  try {
    const response = await fetch('/api/tasks/?status=running')
    if (response.ok) {
      const tasks = await response.json()
      runningCount.value = Array.isArray(tasks) ? tasks.length : 0
    }
  } catch { }
}

async function loadVolumeConfig() {
  try {
    const response = await fetch('/api/volume/status')
    if (response.ok) {
      const data = await response.json()
      snapshotsEnabled.value = data.enabled === true
    }
  } catch { }
}

async function logout() {
  await fetch('/api/logout', { method: 'POST' })
  emit('logout')
}

onMounted(() => {
  pollRunning()
  loadVolumeConfig()
  loadBenchName()
  pollTimer = setInterval(pollRunning, 4000)
})
onUnmounted(() => clearInterval(pollTimer))
</script>

<template>
  <div>
    <Sidebar :header="header" :sections="sections" disableCollapse>
      <template #sidebar-item="{ item }">
        <SidebarItem :label="item.label" :icon="item.icon" :to="item.to" :isActive="isActive(item.to)">
          <template v-if="item.to === '/tasks' && runningCount > 0" #suffix>
            <span
              class="flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-ink-gray-8 px-1 text-[10px] font-bold text-surface-white">
              {{ runningCount }}
            </span>
          </template>
        </SidebarItem>
      </template>
      <template #footer-items />
    </Sidebar>

    <Dialog v-model="showBenchDialog" title="Change Bench" size="sm" :showCloseButton="true">
      <template #default>
        <div class="px-4 pb-6">
          <div v-if="benches.length === 0" class="text-sm text-ink-gray-5 py-2">
            No benches found.
          </div>
          <div v-else class="flex flex-col gap-1">
            <button
              v-for="bench in benches"
              :key="bench.port"
              :disabled="!bench.running && String(bench.port) !== String(currentPort)"
              class="flex items-center justify-between rounded-lg px-3 py-2.5 text-sm transition-colors w-full text-left"
              :class="String(bench.port) === String(currentPort)
                ? 'bg-surface-gray-2 text-ink-gray-9 font-medium cursor-default'
                : bench.running
                  ? 'text-ink-gray-7 hover:bg-surface-gray-2 cursor-pointer'
                  : 'text-ink-gray-4 cursor-not-allowed'"
              @click="switchBench(bench)"
            >
              <span>
                {{ bench.name }}
                <span v-if="!bench.running" class="text-ink-gray-4">(stopped)</span>
              </span>
              <LucideCheck v-if="String(bench.port) === String(currentPort)" class="h-4 w-4 text-ink-green-3" />
            </button>
          </div>
        </div>
      </template>
    </Dialog>

    <Dialog v-model="showNewBenchDialog" title="New Bench" size="sm" :showCloseButton="true">
      <template #default>
        <div class="px-4 flex flex-col gap-4">
          <div autofocus>
            <FormControl
              label="Bench name"
              type="text"
              v-model="newBenchName"
              placeholder="my-bench"
              @input="newBenchError = ''"
              @keyup.enter="createBench"
            />
          </div>
          <ErrorMessage v-if="newBenchError" :message="newBenchError" />
          <p v-if="newBenchStatus" class="text-sm text-ink-gray-6">{{ newBenchStatus }}</p>
          <div class="flex justify-end gap-2 border-t border-outline-gray-1 pt-3">
            <Button variant="ghost" @click="showNewBenchDialog = false">Cancel</Button>
            <Button variant="solid" :loading="newBenchCreating" @click="createBench">Create</Button>
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>
