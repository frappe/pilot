<script setup>
import { Sidebar, SidebarItem } from 'frappe-ui'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import LucideActivity from '~icons/lucide/activity'
import LucideCamera from '~icons/lucide/camera'
import LucideDatabase from '~icons/lucide/database'
import LucideFileText from '~icons/lucide/file-text'
import LucideGlobe from '~icons/lucide/globe'
import LucideLayoutDashboard from '~icons/lucide/layout-dashboard'
import LucideListTodo from '~icons/lucide/list-todo'
import LucidePackage2 from '~icons/lucide/package-2'

const route = useRoute()

const header = {
  title: 'Bench',
  logo: '/logos/frappe-icon.png',
}

const baseNavItems = [
  { label: 'Dashboard', to: '/', icon: LucideLayoutDashboard },
  { label: 'Apps', to: '/apps', icon: LucidePackage2 },
  { label: 'Sites', to: '/sites', icon: LucideGlobe },
  { label: 'Processes', to: '/processes', icon: LucideActivity },
  { label: 'Logs', to: '/logs', icon: LucideFileText },
  { label: 'Database', to: '/database', icon: LucideDatabase },
  { label: 'Tasks', to: '/tasks', icon: LucideListTodo },
]

const snapshotsEnabled = ref(false)

const navItems = computed(() => [
  ...baseNavItems,
  ...(snapshotsEnabled.value ? [{ label: 'Snapshots', to: '/snapshots', icon: LucideCamera }] : []),
])

const sections = computed(() => [{ items: navItems.value }])

function isActive(to) {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}

const runningCount = ref(0)
let pollTimer = null

async function pollRunning() {
  try {
    const res = await fetch('/api/tasks/?status=running')
    if (res.ok) {
      const tasks = await res.json()
      runningCount.value = Array.isArray(tasks) ? tasks.length : 0
    }
  } catch { }
}

async function loadVolumeConfig() {
  try {
    const res = await fetch('/api/volume/status')
    if (res.ok) {
      const data = await res.json()
      console.log(data)
      snapshotsEnabled.value = data.enabled === true
    }
  } catch { }
}

onMounted(() => {
  pollRunning()
  loadVolumeConfig()
  pollTimer = setInterval(pollRunning, 4000)
})
onUnmounted(() => clearInterval(pollTimer))
</script>

<template>
  <Sidebar :header="header" :sections="sections" disableCollapse>
    <template #sidebar-item="{ item }">
      <SidebarItem :label="item.label" :icon="item.icon" :to="item.to" :isActive="isActive(item.to)">
        <template v-if="item.to === '/tasks' && runningCount > 0" #suffix>
          <span
            class="flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-blue-500 px-1 text-[10px] font-bold text-white">
            {{ runningCount }}
          </span>
        </template>
      </SidebarItem>
    </template>
  </Sidebar>
</template>
