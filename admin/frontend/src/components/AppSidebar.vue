<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { Sidebar, SidebarItem } from 'frappe-ui'
import LucideLayoutDashboard from '~icons/lucide/layout-dashboard'
import LucidePackage2 from '~icons/lucide/package-2'
import LucideGlobe from '~icons/lucide/globe'
import LucideActivity from '~icons/lucide/activity'
import LucideFileText from '~icons/lucide/file-text'
import LucideDatabase from '~icons/lucide/database'
import LucideListTodo from '~icons/lucide/list-todo'

const route = useRoute()

const header = {
  title: 'Bench',
  logo: '/logos/frappe-icon.png',
}

const navItems = [
  { label: 'Dashboard', to: '/', icon: LucideLayoutDashboard },
  { label: 'Apps', to: '/apps', icon: LucidePackage2 },
  { label: 'Sites', to: '/sites', icon: LucideGlobe },
  { label: 'Processes', to: '/processes', icon: LucideActivity },
  { label: 'Logs', to: '/logs', icon: LucideFileText },
  { label: 'Database', to: '/database', icon: LucideDatabase },
  { label: 'Tasks', to: '/tasks', icon: LucideListTodo },
]

const sections = [{ items: navItems }]

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
  } catch {}
}

onMounted(() => {
  pollRunning()
  pollTimer = setInterval(pollRunning, 4000)
})
onUnmounted(() => clearInterval(pollTimer))
</script>

<template>
  <Sidebar :header="header" :sections="sections" disableCollapse>
    <template #sidebar-item="{ item }">
      <SidebarItem
        :label="item.label"
        :icon="item.icon"
        :to="item.to"
        :isActive="isActive(item.to)"
      >
        <template v-if="item.to === '/tasks' && runningCount > 0" #suffix>
          <span class="flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-blue-500 px-1 text-[10px] font-bold text-white">
            {{ runningCount }}
          </span>
        </template>
      </SidebarItem>
    </template>
  </Sidebar>
</template>
