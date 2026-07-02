<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Sidebar, SidebarItem, useTheme } from 'frappe-ui'
import { sidebarSections } from '@/navigation'
import { authApi } from '@/api/auth'
import { useIsMobile } from '@/composables/useIsMobile'
import SettingsDialog from '@/components/SettingsDialog.vue'
import BenchSwitcherDialog from '@/components/BenchSwitcherDialog.vue'
import NewBenchDialog from '@/components/NewBenchDialog.vue'
const { setTheme } = useTheme()

const route = useRoute()
const router = useRouter()
const sections = sidebarSections()
const isMobile = useIsMobile()

const showSettings = ref(false)
const showBenches = ref(false)
const showNewBench = ref(false)

function isActive(to) {
  const target = router.resolve(to)
  return target.name ? route.name === target.name : route.path === target.path
}

async function logout() {
  await authApi.logout()
  window.location.reload()
}

// The Settings dialog's sidebar + content layout doesn't adapt to small
// screens, so the entry point is hidden on mobile rather than shipping a
// broken dialog.
const header = computed(() => ({
  title: 'Pilot',
  menuItems: [
    {
      label: 'Central',
      icon: 'lucide-cloud',
    },
    {
      label: 'Settings',
      icon: 'lucide-settings',
      onClick: () => (showSettings.value = true),
    },
    {
      label: 'Switch Bench',
      icon: 'lucide-repeat',
      onClick: () => (showBenches.value = true),
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
}))
</script>

<template>
  <Sidebar :header="header" :sections="sections" class="border-outline-alpha-gray-1">
    <template #sidebar-item="{ item }">
      <SidebarItem v-bind="item" :isActive="isActive(item.to)"
        :class="isActive(item.to) ? '!text-ink-gray-9' : '!text-ink-gray-7'" />
    </template>
    <template #header-logo>
      <svg width="32" height="32" viewBox="0 0 118 118" fill="none" xmlns="http://www.w3.org/2000/svg">
        <g clip-path="url(#clip0_2001_9)">
          <path
            d="M93.9278 0H23.1013C10.3428 0 0 10.3428 0 23.1013V93.9278C0 106.686 10.3428 117.029 23.1013 117.029H93.9278C106.686 117.029 117.029 106.686 117.029 93.9278V23.1013C117.029 10.3428 106.686 0 93.9278 0Z"
            fill="#4C5A67" />
          <path d="M27 47.1855L90.4535 37.6794L54.0932 90.5437L55.6271 59.4414L27 47.1855Z" stroke="white"
            stroke-width="8.92551" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M55.6273 59.4422L79.5424 44.4661" stroke="white" stroke-width="8.92551" stroke-linecap="round"
            stroke-linejoin="round" />
        </g>
        <defs>
          <clipPath id="clip0_2001_9">
            <rect width="118" height="118" fill="white" />
          </clipPath>
        </defs>
      </svg>
    </template>
  </Sidebar>
  <SettingsDialog v-model="showSettings" />
  <BenchSwitcherDialog v-model="showBenches" @new-bench="showNewBench = true" />
  <NewBenchDialog v-model="showNewBench" />
</template>
