<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Sidebar, SidebarItem, useTheme } from 'frappe-ui'
import { sidebarSections } from '@/navigation'
import { authApi } from '@/api/auth'
import { useSession } from '@/composables/auth/useSession'
import { useIsMobile } from '@/composables/common/useIsMobile'
import SettingsDialog from '@/components/settings/SettingsDialog.vue'
import BenchSwitcherDialog from '@/components/benches/BenchSwitcherDialog.vue'
import NewBenchDialog from '@/components/benches/NewBenchDialog.vue'
import PilotLogo from '@/components/common/PilotLogo.vue'
const { setTheme } = useTheme()

const route = useRoute()
const router = useRouter()
const sections = sidebarSections()
const isMobile = useIsMobile()
const { session } = useSession()

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
    // Managing other benches is gated server-wide by admin.allow_bench_management.
    ...(session.allowBenchManagement ? [{
      label: 'Switch Bench',
      icon: 'lucide-repeat',
      onClick: () => (showBenches.value = true),
    }] : []),
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
      <PilotLogo class="size-8" />
    </template>
  </Sidebar>
  <SettingsDialog v-model="showSettings" />
  <template v-if="session.allowBenchManagement">
    <BenchSwitcherDialog v-model="showBenches" @new-bench="showNewBench = true" />
    <NewBenchDialog v-model="showNewBench" />
  </template>
</template>
