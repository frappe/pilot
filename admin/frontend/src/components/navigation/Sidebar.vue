<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Sidebar, SidebarItem } from 'frappe-ui'
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

const header = computed(() => ({ title: 'Pilot', menuItems: menuItems.value }))
</script>

<template>
  <Sidebar :header="isMobile ? undefined : header" :sections="sidebarSections" :disable-collapse="isMobile"
    class="dark:border-outline-gray-2" :class="isMobile ? '!w-full !border-r-0 mobile-sidebar' : ''">
    <template #sidebar-item="{ item }">
      <SidebarItem :label="item.label" :icon="item.icon" :to="item.to" :isActive="isActive(item.to)"
        :class="isActive(item.to) ? '!text-ink-gray-9' : '!text-ink-gray-7'" />
    </template>

    <template v-if="!isMobile" #header-logo>
      <PilotLogo class="size-8" />
    </template>
  </Sidebar>
</template>
