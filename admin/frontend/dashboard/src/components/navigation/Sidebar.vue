<script setup lang="ts">
import {
  KeyboardShortcut,
  Sidebar,
  SidebarCollapseToggle,
  SidebarHeader,
  SidebarItem,
  SidebarLabel,
} from 'frappe-ui'
import { computed, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import Collapsable from '@/components/common/Collapsable.vue'
import PilotLogo from '@/components/icons/Pilot.vue'
import { sidebarSections } from '@/components/navigation/list'
import SetupChecklist from '@/components/navigation/SetupChecklist.vue'
import { useAppMenu } from '@/components/navigation/useAppMenu'
import NotificationsPanel from '@/components/notifications/NotificationsPanel.vue'
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

const isSidebarCollapsed = ref<boolean | null>(null)
const openedSections = reactive<Record<string, boolean>>(
  Object.fromEntries(sidebarSections.map((section) => [section.label || '', true])),
)

const isActive = (to: string) => route.path === to || route.path.startsWith(`${to}/`)
</script>

<template>
  <Sidebar
    v-model:collapsed="isSidebarCollapsed"
    :collapsible="!isMobile"
    class="group border-r"
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
      <SidebarItem v-if="!isMobile" icon="lucide-search" class="mb-0.5 text-sm" @click="openSearch">
        Search

        <template #suffix>
          <KeyboardShortcut combo="Mod+K" class="mr-2" />
        </template>
      </SidebarItem>

      <NotificationsPanel v-if="!isMobile" />

      <template v-for="section in visibleSections" :key="section.label || 'main'">
        <Collapsable v-if="section.label" v-model:opened="openedSections[section.label]">
          <template #header="{ opened, toggle }">
            <SidebarLabel
              divider
              class="mt-3 cursor-pointer *:text-sm-medium"
              :aria-expanded="opened"
              @click="toggle"
            >
              {{ section.label }}

              <span
                v-if="!isSidebarCollapsed"
                class="top-1.5 right-1 absolute size-4 text-ink-gray-4 transition-transform lucide-chevron-right"
                :class="{ 'rotate-90': opened }"
              />
            </SidebarLabel>
          </template>

          <SidebarItem
            v-for="item in section.items"
            :key="item.to"
            :icon="item.icon"
            :route="item.to"
            :active="isActive(item.to)"
            class="mb-0.5 text-sm"
          >
            {{ item.label }}

            <span
              v-if="isMobile"
              class="size-4 text-ink-gray-4 ml-auto mr-1 lucide-chevron-right"
            />
          </SidebarItem>
        </Collapsable>

        <template v-else>
          <SidebarItem
            v-for="item in section.items"
            :key="item.to"
            :icon="item.icon"
            :route="item.to"
            :active="isActive(item.to)"
            class="mb-0.5 text-sm"
          >
            {{ item.label }}

            <span
              v-if="isMobile"
              class="size-4 text-ink-gray-4 ml-auto mr-1 lucide-chevron-right"
            />
          </SidebarItem>
        </template>
      </template>
    </nav>

    <SetupChecklist />

    <SidebarCollapseToggle v-if="!isMobile" class="mt-auto mx-2 mb-2" />
  </Sidebar>
</template>
