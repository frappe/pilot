<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Dialog, Button } from 'frappe-ui'

import General from '@/components/settings/General.vue'
import Database from '@/components/settings/Database.vue'
import Security from '@/components/settings/Security.vue'
import Sessions from '@/components/settings/Sessions.vue'
import SystemInfo from '@/components/settings/SystemInfo.vue'

import { hasUnsavedChanges } from '@/composables/common/useUnsavedChanges'
import { useIsMobile } from '@/composables/common/useIsMobile'

import {
  DATABASE_SECTIONS,
  GENERAL_SECTIONS,
  SECURITY_SECTIONS,
} from '@/components/settings/sections'

const openModel = defineModel()

const isMobile = useIsMobile()
const route = useRoute()
const router = useRouter()

// Every exit funnels through here. Panel switching is a route *param* change
// on one record, so onBeforeRouteLeave never fires for it.
const showDiscard = ref(false)
let pendingNav = null
const guarded = (action) => {
  if (!hasUnsavedChanges()) return action()
  pendingNav = action
  showDiscard.value = true
}
const discardAndGo = () => {
  // Read before closing: closing trips the watcher below, which nulls it.
  const action = pendingNav
  pendingNav = null
  showDiscard.value = false
  action?.()
}
watch(showDiscard, (shown) => {
  if (!shown) pendingNav = null
})

// Closing is guarded; opening never is.
const open = computed({
  get: () => openModel.value,
  set: (value) => {
    if (value) openModel.value = true
    else guarded(() => (openModel.value = false))
  },
})

const sections = computed(() => [
  { id: 'general', label: 'General', icon: 'lucide-settings' },
  { id: 'database', label: 'Database', icon: 'lucide-database' },
  { id: 'security', label: 'Security', icon: 'lucide-shield' },
  { id: 'sessions', label: 'Sessions', icon: 'lucide-monitor' },
  { id: 'system-info', label: 'System Info', icon: 'lucide-info' },
])
// Section and sub-section are routed, so views are deep-linkable.
const activeSection = computed({
  get: () => route.params.section || null,
  set: (id) =>
    router.push(id ? { name: 'Settings', params: { section: id } } : { name: 'Settings' }),
})
const currentSection = computed(() => activeSection.value ?? sections.value[0].id)
const activeSectionLabel = computed(
  () => sections.value.find((s) => s.id === currentSection.value)?.label,
)

const subSectionOptions = computed(() => {
  if (currentSection.value === 'general') return GENERAL_SECTIONS
  if (currentSection.value === 'database') return DATABASE_SECTIONS
  if (currentSection.value === 'security') return SECURITY_SECTIONS
  return []
})
const subSection = computed({
  get: () => subSectionOptions.value.find((s) => s.id === route.params.subSection) ?? null,
  set: (section) =>
    router.push({
      name: 'Settings',
      params: { section: currentSection.value, subSection: section?.id },
    }),
})
// Guarded here, not in `subSection`, so goBack() can move without re-asking.
const guardedSubSection = computed({
  get: () => subSection.value,
  set: (section) => guarded(() => (subSection.value = section)),
})

// For Sessions the :subSection route slot carries a jti instead.
const sessionJti = computed({
  get: () => (currentSection.value === 'sessions' ? route.params.subSection || null : null),
  set: (jti) =>
    router.push({
      name: 'Settings',
      params: { section: 'sessions', subSection: jti || undefined },
    }),
})

// Reset on section change so a stale title is never inherited.
const nestedView = ref(null)
watch(currentSection, () => (nestedView.value = null))

const headerTitle = computed(() => {
  if (sessionJti.value) return nestedView.value?.title ?? sessionJti.value
  return subSection.value?.label ?? activeSectionLabel.value
})

const goBack = () => {
  guarded(() => {
    if (sessionJti.value) sessionJti.value = null
    else if (subSection.value) subSection.value = null
    else activeSection.value = null
  })
}
</script>

<template>
  <Dialog v-model="open" bare size="5xl">
    <template #default="{ close }">
      <div class="relative flex sm:h-[calc(100vh-6rem)] max-h-[calc(100vh-6rem)]">
        <aside
          class="flex-col bg-surface-sidebar p-2 sm:border-r dark:border-outline-gray-2 w-full sm:w-[220px] shrink-0"
          :class="activeSection ? 'hidden sm:flex' : 'flex'"
        >
          <h3 class="mb-1 p-2 pb-3 border-b sm:border-b-0 border-outline-gray-1 font-semibold">
            Settings
          </h3>

          <!-- close btn only for mobile -->
          <Button
            v-if="!activeSection"
            class="sm:hidden top-3 right-3 absolute"
            variant="ghost"
            icon="lucide-x"
            label="Close settings"
            tooltip="Close"
            @click="close"
          />

          <Button
            v-for="section in sections"
            :key="section.id"
            :variant="isMobile ? 'subtle' : 'ghost'"
            :size="isMobile ? 'md' : 'sm'"
            class="!justify-start border sm:border-0 w-full mt-0.5"
            :class="
                currentSection === section.id
                  ? 'sm:!bg-surface-elevation-3 sm:!shadow-sm sm:!text-ink-gray-9 !text-ink-gray-6'
                  : '!text-ink-gray-6'
              "
            @click="guarded(() => (activeSection = section.id))"
          >
            <template #prefix>
              <span :class="section.icon" class="size-4" />
            </template>
            {{ section.label }}
          </Button>
        </aside>

        <div
          class="flex-col flex-1 px-6 sm:px-14 pt-6 sm:pt-10 pb-10 sm:pb-12 overflow-y-auto"
          :class="activeSection ? 'flex' : 'hidden sm:flex'"
        >
          <!-- header -->
          <div class="flex items-center gap-2 pb-4">
            <Button
              v-if="subSection || sessionJti || activeSection"
              class="-ml-2"
              :class="{ 'sm:hidden': !subSection && !sessionJti }"
              variant="ghost"
              icon="lucide-arrow-left"
              label="Back"
              tooltip="Back"
              @click="goBack"
            />

            <h3 class="text-lg-semibold text-ink-gray-9">{{ headerTitle }}</h3>

            <div id="settings-header-actions" class="flex items-center gap-2 ml-auto" />
          </div>

          <General v-if="currentSection === 'general'" v-model:open-section="guardedSubSection" />
          <Database
            v-else-if="currentSection === 'database'"
            v-model:open-section="guardedSubSection"
          />
          <Security
            v-else-if="currentSection === 'security'"
            v-model:open-section="guardedSubSection"
          />
          <Sessions
            v-else-if="currentSection === 'sessions'"
            v-model:nested-view="nestedView"
            v-model:jti="sessionJti"
          />
          <SystemInfo v-else-if="currentSection === 'system-info'" />
        </div>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showDiscard" title="Unsaved changes" size="sm">
    <p class="text-ink-gray-7 text-p-base">
      You have changes here that have not been saved. Leaving loses them.
    </p>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button @click="showDiscard = false">Keep editing</Button>
        <Button variant="solid" theme="red" @click="discardAndGo">Discard</Button>
      </div>
    </template>
  </Dialog>
</template>
