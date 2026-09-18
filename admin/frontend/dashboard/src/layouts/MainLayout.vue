<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  Breadcrumbs,
  BottomSheet,
  DesktopShell,
  MobileShell,
  MobileNav,
  MobileNavItem,
} from 'frappe-ui'

import Sidebar from '@/components/navigation/Sidebar.vue'
import PilotLogo from '@/components/icons/Pilot.vue'
import UpdateStatusButton from '@/components/common/UpdateStatusButton.vue'
import SettingsDialog from '@/components/settings/SettingsDialog.vue'
import BenchSwitcherDialog from '@/components/benches/BenchSwitcherDialog.vue'
import NewBenchDialog from '@/components/benches/NewBenchDialog.vue'
import SearchDialog from '@/components/search/SearchDialog.vue'
import NotificationsPanel from '@/components/notifications/NotificationsPanel.vue'

import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { useSession } from '@/composables/auth/useSession'
import { useAppMenu } from '@/components/navigation/useAppMenu'
import { openSearch, searchOpen, useSearchShortcut } from '@/composables/common/useSearch'

const route = useRoute()
const router = useRouter()
const isMobile = useIsMobile()

const { session } = useSession()
const { showBenches, showNewBench } = useAppMenu()
const { items, resetBreadcrumbs } = useBreadcrumbs()

useSearchShortcut()

// Remembers the last non-Settings route so dismissing the dialog (backdrop
// click, Escape, close button) exits to it directly instead of stepping back
// through every section/subsection push made while the dialog was open.
const lastNonSettingsRoute = ref(null)

watch(
  () => route.fullPath,
  () => {
    if (route.name !== 'Settings') lastNonSettingsRoute.value = route.fullPath
  },
  { immediate: true },
)

const showSettings = computed({
  get: () => route.name === 'Settings',
  set: (value) => {
    if (!value) router.push(lastNonSettingsRoute.value || { name: 'Sites' })
  },
})

const mobileNavDrawer = ref(false)

watch(
  () => route.name,
  () => {
    resetBreadcrumbs()
    mobileNavDrawer.value = false
  },
)

const breadcrumbs = computed(() => {
  const all = items.value || breadcrumbsFromRouteMeta(route.meta)
  return isMobile.value ? all.slice(-1) : all
})

// The group is only a sidebar section heading - it has no route of its own, so
// rendering it as a crumb gives a dead link that leads nowhere.
const breadcrumbsFromRouteMeta = ({ title = '' }) => {
  return title ? [{ label: title }] : []
}
</script>

<template>
  <MobileShell v-if="isMobile">
    <header
      class="sticky top-0 z-20 flex min-h-12 flex-col justify-center border-b border-outline-gray-2 bg-surface-base px-3 md:px-4"
    >
      <div class="flex items-center justify-between">
        <div v-if="route.name == 'Home'" class="flex items-center gap-2">
          <PilotLogo class="size-6 rounded-1" />
          <span>Home</span>
        </div>

        <button
          v-else
          class="flex items-center gap-1 max-w-[50%] min-w-0"
          @click="mobileNavDrawer = true"
        >
          <Breadcrumbs :items="breadcrumbs" class="min-w-0" />
          <span class="size-4 text-ink-gray-5 shrink-0 lucide-chevron-down" />
        </button>

        <div id="header-badge" class="flex items-center pl-1" />
        <div id="header-actions" class="flex items-center gap-2 ml-auto">
          <UpdateStatusButton v-if="route.meta.showUpdateStatus" />
        </div>
      </div>
    </header>

    <main>
      <slot />
    </main>

    <template #nav>
      <MobileNav class="!bg-surface-base">
        <MobileNavItem label="Home" icon="lucide-house" to="/home" :active="route.name == 'Home'" />
        <MobileNavItem label="Search" icon="lucide-search" @click="openSearch" />
        <NotificationsPanel mobile />
        <MobileNavItem
          label="Settings"
          icon="lucide-settings"
          to="/mobile/settings"
          :active="route.name == 'MobileSettings'"
        />
      </MobileNav>
    </template>

    <BottomSheet v-model:open="mobileNavDrawer">
      <Sidebar is-mobile class="p-2" />
    </BottomSheet>
  </MobileShell>

  <DesktopShell v-else class="h-screen">
    <template #sidebar>
      <Sidebar />
    </template>

    <header
      class="sticky top-0 z-20 flex min-h-12 flex-col justify-center border-b border-outline-gray-2 bg-surface-base px-3 md:px-4"
    >
      <div class="flex flex-1 items-center gap-1">
        <Breadcrumbs :items="breadcrumbs" />
        <div id="header-badge" class="flex items-center" />
        <div id="header-actions" class="flex items-center gap-2 ml-auto">
          <UpdateStatusButton v-if="route.meta.showUpdateStatus" />
        </div>
      </div>
    </header>

    <slot />
  </DesktopShell>

  <SettingsDialog v-model="showSettings" />

  <template v-if="session.allowBenchManagement">
    <BenchSwitcherDialog v-model="showBenches" @new-bench="showNewBench = true" />
    <NewBenchDialog v-model="showNewBench" />
  </template>

  <SearchDialog v-model:open="searchOpen" />
</template>
