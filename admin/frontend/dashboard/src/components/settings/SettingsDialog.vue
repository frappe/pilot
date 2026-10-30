<script setup lang="ts">
import { Button, Dialog } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Database from '@/components/settings/Database.vue'
import General from '@/components/settings/General.vue'
import Git from '@/components/settings/Git.vue'
import LLM from '@/components/settings/LLM.vue'
import Mail from '@/components/settings/Mail.vue'
import Notifications from '@/components/settings/Notifications.vue'
import S3Bucket from '@/components/settings/S3Bucket.vue'
import Security from '@/components/settings/Security.vue'
import Sessions from '@/components/settings/Sessions.vue'
import SystemInfo from '@/components/settings/SystemInfo.vue'
import {
  DATABASE_SECTIONS,
  GIT_SECTIONS,
  SECURITY_SECTIONS,
  SETTINGS_GROUPS,
  SETTINGS_PAGES,
} from '@/components/settings/sections'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { hasUnsavedChanges } from '@/composables/common/useUnsavedChanges'

const openModel = defineModel<boolean>()

const isMobile = useIsMobile()
const route = useRoute()
const router = useRouter()

// Every exit funnels through here. Panel switching is a route *param* change
// on one record, so onBeforeRouteLeave never fires for it.
const showDiscard = ref(false)
let pendingNav: (() => unknown) | null = null
const guarded = (action: () => unknown) => {
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

// Section and sub-section are routed, so views are deep-linkable.
const activeSection = computed({
  get: () => route.params.section || null,
  set: (id) =>
    router.push(id ? { name: 'Settings', params: { section: id } } : { name: 'Settings' }),
})
const currentSection = computed(() => activeSection.value ?? SETTINGS_PAGES[0].id)
const activeSectionLabel = computed(
  () => SETTINGS_PAGES.find((s) => s.id === currentSection.value)?.label,
)

const subSectionOptions = computed(() => {
  if (currentSection.value === 'github') return GIT_SECTIONS
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
const nestedSubSection = computed(() =>
  currentSection.value === 'security' ? null : subSection.value,
)

// Guarded here, not in `subSection`, so goBack() can move without re-asking.
const guardedSubSection = computed({
  get: () => subSection.value,
  set: (section) => guarded(() => (subSection.value = section)),
})

// For Sessions the :subSection route slot carries a jti instead.
const sessionJti = computed({
  get: () => {
    if (currentSection.value !== 'sessions') return null

    const target = route.params.subSection
    return typeof target === 'string' && target ? target : null
  },
  set: (jti) =>
    router.push({
      name: 'Settings',
      params: { section: 'sessions', subSection: jti || undefined },
    }),
})

// Reset on section change so a stale title is never inherited.
const nestedView = ref<{ title: string } | null>(null)
watch(currentSection, () => (nestedView.value = null))

const headerTitle = computed(() => {
  if (sessionJti.value) return nestedView.value?.title ?? sessionJti.value
  return nestedSubSection.value?.label ?? activeSectionLabel.value
})

const goBack = () => {
  guarded(() => {
    if (sessionJti.value) sessionJti.value = null
    else if (nestedSubSection.value)
      subSection.value =
        subSectionOptions.value.find((s) => s.id === nestedSubSection.value?.parent) ?? null
    else activeSection.value = null
  })
}
</script>

<template>
  <Dialog v-model="open" bare size="5xl">
    <template #default="{ close }">
      <div class="relative flex sm:h-[calc(100vh-6rem)] max-h-[calc(100vh-6rem)]">
        <aside
          class="flex-col bg-surface-gray-1 p-2 w-full sm:w-[220px] shrink-0"
          :class="activeSection ? 'hidden sm:flex' : 'flex'"
        >
          <h3 class="sm:hidden flex items-center mb-1 px-2 h-7 text-base-semibold text-ink-gray-9">
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

          <nav class="flex flex-col flex-1 gap-5 overflow-y-auto">
            <section
              v-for="group in SETTINGS_GROUPS"
              :key="group.label"
              class="flex flex-col gap-0.5 last:mt-auto"
            >
              <h4
                v-if="group.label"
                class="flex items-center px-2 h-7 text-ink-gray-8 text-sm-semibold"
              >
                {{ group.label }}
              </h4>

              <Button
                v-for="page in group.pages"
                :key="page.id"
                :variant="isMobile ? 'subtle' : 'ghost'"
                :size="isMobile ? 'md' : 'sm'"
                class="!justify-start border sm:border-0 w-full"
                :class="
                  currentSection === page.id
                    ? 'sm:!bg-surface-gray-3 !text-ink-gray-9'
                    : '!text-ink-gray-6'
                "
                @click="guarded(() => (activeSection = page.id))"
              >
                <template #prefix>
                  <span :class="page.icon" class="size-4" />
                </template>
                {{ page.label }}
              </Button>
            </section>
          </nav>
        </aside>

        <div
          class="flex-col flex-1 px-6 sm:px-10 pt-6 sm:pt-9 pb-10 sm:pb-16 overflow-y-auto"
          :class="activeSection ? 'flex' : 'hidden sm:flex'"
        >
          <!-- header -->
          <div class="flex items-center gap-2 pb-6">
            <Button
              v-if="nestedSubSection || sessionJti || activeSection"
              class="-ml-2"
              :class="{ 'sm:hidden': !nestedSubSection && !sessionJti }"
              variant="ghost"
              icon="lucide-arrow-left"
              label="Back"
              tooltip="Back"
              @click="goBack"
            />

            <h3 class="text-xl-semibold text-ink-gray-9">{{ headerTitle }}</h3>

            <div id="settings-header-actions" class="flex items-center gap-2 ml-auto" />
          </div>

          <General v-if="currentSection === 'bench'" />
          <Git v-else-if="currentSection === 'github'" v-model:open-section="guardedSubSection" />
          <S3Bucket v-else-if="currentSection === 's3-bucket'" />
          <LLM v-else-if="currentSection === 'llm'" />
          <Mail v-else-if="currentSection === 'mail'" />
          <Notifications v-else-if="currentSection === 'notifications'" />

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
