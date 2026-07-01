<template>
  <UpdatesAvailableButton />

  <div v-if="loading" class="flex justify-center py-12">
    <LoadingText />
  </div>
  <div v-else-if="error" class="py-12">
    <ErrorMessage :message="error" />
  </div>
  <div v-else-if="site" class="mx-auto w-full max-w-3xl">
    <!-- Hero -->
    <div class="relative -mx-6 -mt-6 px-6 pt-6 pb-7 overflow-hidden">
      <div class="absolute inset-0 pointer-events-none dot-field" aria-hidden="true" />
      <div
        class="relative flex justify-between items-center gap-3 bg-surface-base p-4 border rounded-xl border-outline-gray-2">
        <div class="flex items-center gap-3 min-w-0">
          <span
            class="place-items-center grid bg-surface-elevation-1 border rounded-xl border-outline-gray-2 size-10 sm:size-12 text-ink-gray-6 shrink-0">
            <span class="size-5 sm:size-6 lucide-globe" />
          </span>
          <div class="min-w-0">
            <div class="flex items-center gap-2 min-w-0">
              <h1 class="font-semibold text-ink-gray-9 text-base sm:text-xl truncate">{{ site.name }}</h1>
              <Badge :label="statusLabel" :theme="statusBadgeTheme" variant="subtle" size="md" class="shrink-0" />
            </div>
            <div class="hidden sm:flex items-center gap-1.5 mt-1 text-ink-gray-5 text-sm">
              <span class="size-3.5 lucide-box" />
              {{ version || 'Version —' }}
            </div>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <Button size="sm" class="hidden sm:flex" @click="goToMarketplace">
            <template #prefix><span class="size-4 lucide-plus" /></template>
            Install app
          </Button>
          <Dropdown :options="menuOptions" placement="bottom-end">
            <template #default="{ open }">
              <Button variant="subtle" size="sm" :active="open">
                <span class="size-4 lucide-ellipsis" />
              </Button>
            </template>
          </Dropdown>
        </div>
      </div>
    </div>

    <!-- Tabs -->
    <TabButtons v-model="activeTab" :options="tabs" />

    <!-- Sections -->
    <SiteApps v-if="activeTab === 'apps'" :site-name="siteName" />
    <SiteBackups v-else-if="activeTab === 'backups'" :site-name="siteName" />
    <SiteConfig v-else-if="activeTab === 'config'" :site-name="siteName" />
    <SiteSettings v-else-if="activeTab === 'settings'" :site-name="siteName" />
  </div>

  <Teleport defer to="#header-actions">
    <Button variant="subtle" size="sm" @click="openSite">
      <template #prefix><span class="size-4 lucide-external-link" /></template>
      <span class="hidden sm:inline">Open site</span>
      <span class="sm:hidden">Open</span>
    </Button>
  </Teleport>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge, Button, Dropdown, ErrorMessage, LoadingText, TabButtons } from 'frappe-ui'
import UpdatesAvailableButton from '@/components/UpdatesAvailableButton.vue'
import SiteApps from '@/components/sites/Apps.vue'
import SiteBackups from '@/components/sites/Backups.vue'
import SiteConfig from '@/components/sites/Config.vue'
import SiteSettings from '@/components/sites/Settings.vue'
import { useBreadcrumbs } from '@/composables/useBreadcrumbs'
import { useSite } from '@/composables/useSite'
import { useBench } from '@/composables/useBench'

const route = useRoute()
const router = useRouter()
const siteName = route.params.name

const { setBreadcrumbs } = useBreadcrumbs()
const { site, loading, error, status, load, login } = useSite(siteName)
const { version, load: loadBench } = useBench()

setBreadcrumbs([
  { label: 'Sites', route: { name: 'Sites' } },
  { label: siteName },
])

const STATUS_THEMES = { online: 'green', broken: 'red', offline: 'orange' }
const STATUS_LABELS = { online: 'Live', broken: 'Broken', offline: 'Paused' }

const statusLabel = computed(() => STATUS_LABELS[status.value] ?? status.value)
const statusBadgeTheme = computed(() => STATUS_THEMES[status.value] ?? 'gray')

const tabs = [
  { value: 'apps', label: 'Apps' },
  { value: 'backups', label: 'Backups' },
  { value: 'config', label: 'Config' },
  { value: 'settings', label: 'Settings' },
]

const VALID_TABS = tabs.map((t) => t.value)
const activeTab = ref(VALID_TABS.includes(route.params.tab) ? route.params.tab : 'apps')

watch(activeTab, (tab) => {
  router.replace({ name: 'SiteDetail', params: { name: siteName, tab } })
})

watch(() => route.params.tab, (tab) => {
  if (tab && VALID_TABS.includes(tab) && tab !== activeTab.value) activeTab.value = tab
})

const tabLabel = computed(() => tabs.find((t) => t.value === activeTab.value)?.label ?? '')
watchEffect(() => {
  if (site.value) document.title = `${site.value.name} | ${tabLabel.value}`
})

const isMobile = ref(window.innerWidth < 640)
const onResize = () => { isMobile.value = window.innerWidth < 640 }

function openSite() {
  window.open(`https://${site.value.name}`, '_blank')
}

function goToMarketplace() {
  router.push({ path: '/marketplace', query: { site: siteName } })
}

const menuOptions = computed(() => [
  ...(isMobile.value ? [{ label: 'Install app', icon: 'lucide-plus', onClick: goToMarketplace }] : []),
  { label: 'Login as admin', icon: 'lucide-log-in', onClick: login },
  { label: 'Back up now', icon: 'lucide-database-backup', onClick: () => { } },
  { label: 'Deactivate site', icon: 'lucide-power', onClick: () => { } },
])

onMounted(() => {
  load()
  loadBench()
  window.addEventListener('resize', onResize)
})

onUnmounted(() => window.removeEventListener('resize', onResize))
</script>

<style scoped>
.dot-field {
  background-image: radial-gradient(var(--outline-gray-3) 1.1px, transparent 1.3px);
  background-size: 20px 20px;
  background-position: -8px -8px;
  mask-image: linear-gradient(to bottom, rgb(0 0 0 / .95), transparent 90%);
}
</style>
