<script setup>
import { Sidebar, SidebarItem } from 'frappe-ui'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import LucideActivity from '~icons/lucide/activity'
import LucideCamera from '~icons/lucide/camera'
import LucideDatabase from '~icons/lucide/database'
import LucideFileText from '~icons/lucide/file-text'
import LucideGlobe from '~icons/lucide/globe'
import LucideListTodo from '~icons/lucide/list-todo'
import LucideLogOut from '~icons/lucide/log-out'
import LucideStore from '~icons/lucide/store'
import LucideSettings from '~icons/lucide/settings'
import LucideRepeat from '~icons/lucide/repeat'

const emit = defineEmits(['logout', 'open-settings', 'change-bench'])

const route = useRoute()

const header = {
  title: 'Pilot',
  logo: '/logos/pilot-plane.svg',
  menuItems: [
    { label: 'Settings', icon: LucideSettings, onClick: () => emit('open-settings') },
    { label: 'Manage Benches', icon: LucideRepeat, onClick: () => emit('change-bench') },
    { label: 'Logout', icon: LucideLogOut, onClick: () => logout() },
  ],
}

const primaryNavItems = [
  { label: 'Sites', to: '/', icon: LucideGlobe },
  { label: 'Marketplace', to: '/marketplace', icon: LucideStore },
]

const snapshotsEnabled = ref(false)
const runningCount = ref(0)
let pollTimer = null

const sections = computed(() => [
  { items: primaryNavItems },
  {
    label: 'System',
    collapsible: true,
    items: [
      { label: 'Monitor', to: '/monitor', icon: LucideActivity },
      { label: 'Logs', to: '/logs', icon: LucideFileText },
      { label: 'Tasks', to: '/tasks', icon: LucideListTodo },
      { label: 'Database', to: '/database', icon: LucideDatabase },
      ...(snapshotsEnabled.value ? [{ label: 'Snapshots', to: '/snapshots', icon: LucideCamera }] : []),
    ],
  },
])

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
  pollTimer = setInterval(pollRunning, 4000)
})
onUnmounted(() => clearInterval(pollTimer))
</script>

<template>
  <div class="h-full">
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
    </Sidebar>
  </div>
</template>
