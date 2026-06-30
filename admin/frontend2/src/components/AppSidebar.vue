<script setup>
import { useRoute, useRouter } from 'vue-router'
import { Sidebar, SidebarItem, useTheme } from 'frappe-ui'
import { sidebarSections } from '@/navigation'
import { request } from '@/api/client'
const { setTheme } = useTheme()

const route = useRoute()
const router = useRouter()
const sections = sidebarSections()

function isActive(to) {
  const target = router.resolve(to)
  return target.name ? route.name === target.name : route.path === target.path
}

async function logout() {
  await request.post('logout')
  window.location.reload()
}

const header = {
  title: 'Pilot',
  menuItems: [
    {
      label: 'Central',
      icon: 'lucide-cloud',
    },
    {
      label: 'Settings',
      icon: 'lucide-settings',
    },
    {
      label: 'System Info',
      icon: 'lucide-info',
    },
    {
      label: 'Theme',
      icon: 'lucide-sun-moon',
      submenu: [
        { label: 'Light', icon: 'lucide-sun', onClick: () => setTheme('light') },
        { label: 'Dark', icon: 'lucide-moon', onClick: () => setTheme('dark') },
        { label: 'System', icon: 'lucide-monitor', onClick: () => setTheme('system') },
      ],
    },
    { label: 'Logout', icon: 'lucide-log-out', onClick: logout },
  ],
}
</script>

<template>
  <Sidebar :header="header" :sections="sections" class="border-outline-alpha-gray-1">
    <template #sidebar-item="{ item }">
      <SidebarItem
        v-bind="item"
        :isActive="isActive(item.to)"
        :class="isActive(item.to) ? '!text-ink-gray-9' : '!text-ink-gray-7'"
      />
    </template>
    <template #header-logo>
      <span
        data-v-ff074055=""
        class="grid size-7 shrink-0 place-items-center rounded-md bg-[var(--ink-gray-9)] text-ink-base"
        ><span data-v-ff074055="" class="lucide-server size-4"></span
      ></span>
    </template>
  </Sidebar>
</template>
