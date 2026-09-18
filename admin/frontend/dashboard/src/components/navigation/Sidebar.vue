<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Sidebar, SidebarHeader, SidebarLabel, SidebarItem, SidebarCollapseToggle } from 'frappe-ui'

import PilotLogo from '@/components/icons/Pilot.vue'
import NotificationsPanel from '@/components/notifications/NotificationsPanel.vue'

import { sidebarSections } from '@/components/navigation/list'
import { useAppMenu } from '@/components/navigation/useAppMenu'
import { openSearch } from '@/composables/common/useSearch'

interface Props {
  isMobile?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isMobile: false,
})

const route = useRoute()
const { menuItems, session } = useAppMenu()

const visibleSections = computed(() =>
  sidebarSections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => !item.flag || session[item.flag]),
    }))
    .filter((section) => section.items.length),
)

const isActive = (to) => route.path === to || route.path.startsWith(`${to}/`)
</script>

<template>
  <Sidebar
    :disable-collapse="isMobile"
    class="border-r dark:border-outline-gray-2"
    :class="isMobile ? '!w-full !border-r-0 mobile-sidebar bg-transparent' : ''"
  >
    <SidebarHeader
      v-if="!isMobile"
      title="Pilot"
      :subtitle="session.benchName"
      :menu-items="menuItems"
      :logo="PilotLogo"
    />

    <nav class="flex-1 overflow-y-auto px-2 pt-2">
      <SidebarItem
        v-if="!isMobile"
        icon="lucide-search"
        suffix="⌘ K"
        class="mb-0.5 text-sm"
        @click="openSearch"
      >
        Search
      </SidebarItem>

      <NotificationsPanel v-if="!isMobile" />

      <template v-for="section in visibleSections" :key="section.label || 'main'">
        <SidebarLabel v-if="section.label" divider class="mt-2">
          {{ section.label }}
        </SidebarLabel>

        <SidebarItem
          v-for="item in section.items"
          :key="item.to"
          :icon="item.icon"
          :to="item.to"
          :active="isActive(item.to)"
          class="mb-0.5 text-sm"
        >
          {{ item.label }}

          <span v-if="isMobile" class="size-4 text-ink-gray-4 ml-auto mr-1 lucide-chevron-right" />
        </SidebarItem>
      </template>
    </nav>

    <SidebarCollapseToggle v-if="!isMobile" class="mt-auto mx-2 mb-2" />
  </Sidebar>
</template>
