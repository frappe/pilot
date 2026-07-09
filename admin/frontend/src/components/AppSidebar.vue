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
import PilotLogo from '@/components/PilotLogo.vue'
import { useI18n } from '@/i18n'
const { setTheme } = useTheme()

const route = useRoute()
const router = useRouter()
const isMobile = useIsMobile()
const { currentLanguage, languages, setLanguage, t } = useI18n()
const sections = computed(() => sidebarSections(t))

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
  title: t('app.name'),
  menuItems: [
    {
      label: t('layout.central'),
      icon: 'lucide-cloud',
    },
    {
      label: t('layout.settings'),
      icon: 'lucide-settings',
      onClick: () => (showSettings.value = true),
    },
    {
      label: t('layout.switchBench'),
      icon: 'lucide-repeat',
      onClick: () => (showBenches.value = true),
    },
    {
      label: t('common.theme'),
      icon: 'lucide-sun-moon',
      submenu: [
        { label: t('common.theme.light'), icon: 'lucide-sun', onClick: () => setTheme('light') },
        { label: t('common.theme.dark'), icon: 'lucide-moon', onClick: () => setTheme('dark') },
        { label: t('common.system'), icon: 'lucide-monitor', onClick: () => setTheme('system') },
      ],
    },
    {
      label: t('common.language'),
      icon: 'lucide-languages',
      submenu: languages.map((language) => ({
        label: language.label,
        icon: currentLanguage.value === language.value ? 'lucide-check' : 'lucide-circle',
        onClick: () => setLanguage(language.value),
      })),
    },
    { label: t('layout.logout'), icon: 'lucide-log-out', onClick: logout },
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
  <BenchSwitcherDialog v-model="showBenches" @new-bench="showNewBench = true" />
  <NewBenchDialog v-model="showNewBench" />
</template>
