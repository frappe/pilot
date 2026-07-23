<script setup>
import { useRoute } from 'vue-router'
import { Sidebar, SidebarHeader, SidebarLabel, SidebarItem } from 'frappe-ui'
import { sidebarSections } from './list'
import { useAppMenu } from './useAppMenu'
import PilotLogo from '@/components/common/PilotLogo.vue'

const props = defineProps({
  isMobile: { type: Boolean, default: false },
})

const route = useRoute()
const { menuItems } = useAppMenu()

// Prefix match, not just exact: a site's detail page (/sites/foo/general)
// should still light up the "Sites" item, not just the bare list page.
function isActive(to) {
  return route.path === to || route.path.startsWith(`${to}/`)
}
</script>

<template>
  <Sidebar :disable-collapse="isMobile" class="border-r dark:border-outline-gray-2"
    :class="isMobile ? '!w-full !border-r-0 mobile-sidebar bg-transparent' : ''">

    <SidebarHeader v-if="!isMobile" title="Pilot" :menu-items="menuItems">
      <template #logo>
        <PilotLogo class="size-8" />
      </template>
    </SidebarHeader>

    <nav class="flex-1 overflow-y-auto px-2 pt-2">
      <template v-for="section in sidebarSections" :key="section.label || 'main'">
        <SidebarLabel v-if="section.label" class="mt-2">{{ section.label }}</SidebarLabel>
        <SidebarItem v-for="item in section.items" :key="item.to" :icon="item.icon" :to="item.to"
          :active="isActive(item.to)" class="mb-0.5">
          {{ item.label }}

          <lucide-chevron-right v-if='isMobile' class="size-4 text-ink-gray-4 ml-auto mr-1" />
        </SidebarItem>
      </template>
    </nav>
  </Sidebar>
</template>
