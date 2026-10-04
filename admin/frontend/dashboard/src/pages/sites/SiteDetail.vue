<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Badge,
  type BadgeProps,
  Button,
  Dropdown,
  ErrorMessage,
  Skeleton,
  TabButtons,
  toast,
} from 'frappe-ui'

import PageHero from '@/components/common/PageHero.vue'
import StickyToolbar from '@/components/common/StickyToolbar.vue'
import SiteApps from '@/components/sites/Apps.vue'
import SiteBackups from '@/components/sites/Backups.vue'
import SiteConfig from '@/components/sites/Config.vue'
import SiteSettings from '@/components/sites/Settings.vue'
import Activities from '@/pages/Activities.vue'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { useSite } from '@/composables/sites/useSite'
import { useSiteStorage } from '@/composables/sites/useSiteStorage'
import { errorMessage } from '@/utils/error'
import { toSentenceCase } from '@/utils/format'
import { openTaskDetailPage } from '@/utils/taskRoute'

const route = useRoute()
const router = useRouter()
const siteName = Array.isArray(route.params.name) ? route.params.name[0] : route.params.name

const { setBreadcrumbs } = useBreadcrumbs()
const { site, loading, error, status, load, reload, login, backup, apps, loadApps } =
  useSite(siteName)

const { load: loadStorage, storageLabel } = useSiteStorage()
const storageUsed = computed(() => storageLabel(siteName))

setBreadcrumbs([{ label: 'Sites', route: { name: 'Sites' } }, { label: siteName }])

const STATUS_THEMES: Record<string, BadgeProps['theme']> = {
  online: 'green',
  broken: 'red',
  offline: 'amber',
  provisioning: 'blue',
}

const STATUS_LABELS: Record<string, string> = {
  online: 'Active',
  broken: 'Broken',
  offline: 'Paused',
  provisioning: 'Creating',
}

const statusLabel = computed(() => STATUS_LABELS[status.value] ?? status.value)
const statusBadgeTheme = computed(() => STATUS_THEMES[status.value] ?? 'gray')

const tabs = [
  { value: 'apps', label: 'Apps' },
  { value: 'backups', label: 'Backups' },
  { value: 'config', label: 'Config' },
  { value: 'activity', label: 'Activity' },
  { value: 'settings', label: 'Settings' },
]

const VALID_TABS = tabs.map((t) => t.value)
const tabParam = Array.isArray(route.params.tab) ? route.params.tab[0] : route.params.tab
const activeTab = ref(VALID_TABS.includes(tabParam) ? tabParam : 'apps')

watch(activeTab, (tab) => {
  router.replace({ name: 'SiteDetail', params: { name: siteName, tab } })
})

watch(
  () => route.params.tab,
  (tab) => {
    const value = Array.isArray(tab) ? tab[0] : tab
    if (value && VALID_TABS.includes(value) && value !== activeTab.value) activeTab.value = value
  },
)

const tabLabel = computed(() => tabs.find((t) => t.value === activeTab.value)?.label ?? '')
watchEffect(() => {
  if (site.value) document.title = `${site.value.name} | ${tabLabel.value}`
})

const APP_ACTIONS: Record<string, string> = {
  'install-app': 'installed',
  'uninstall-app': 'uninstalled',
}
const appRegistry = useAppRegistry()
const appAction = computed(() => {
  const app = route.query.app
  const action = route.query.action
  if (typeof app !== 'string' || typeof action !== 'string' || !(action in APP_ACTIONS)) return null
  return { app, action }
})
watch(
  appAction,
  async (value) => {
    if (!value) return
    await Promise.all([appRegistry.load(), loadApps()])
    const appDetail = apps.value.find((app) => app.name === value.app)
    const title =
      appRegistry.titleMap.value[value.app] || toSentenceCase(appDetail?.title) || value.app
    toast.success(`${title} ${APP_ACTIONS[value.action]} on ${siteName}`)
    router.replace({ name: 'SiteDetail', params: { name: siteName, tab: activeTab.value } })
  },
  { immediate: true },
)

const isMobile = useIsMobile()

const openSite = () => {
  if (!site.value) return
  window.open(`${site.value.url}/desk`, '_blank')
}

const settingUpSite = ref(false)
const setupSite = async () => {
  settingUpSite.value = true
  try {
    await login()
  } catch (caught) {
    toast.error(errorMessage(caught, 'Could not open the setup wizard'))
  } finally {
    settingUpSite.value = false
  }
}

const goToMarketplace = () => {
  router.push({ path: '/marketplace', query: { site: siteName } })
}

const goToAnalytics = () => {
  router.push({ name: 'Analytics', query: { view: 'site', window: '24h', site: siteName } })
}

const loginAsAdmin = () => {
  toast.promise(login({ onHint: (hint) => toast.info(hint) }), {
    loading: 'Logging in as admin',
    success: 'Logged in as admin',
    error: (caught: unknown) => errorMessage(caught, 'Could not log in as admin'),
  })
}

const backupNow = async () => {
  try {
    const result = await backup()
    if (hasApiError(result)) toast.error(apiErrorMessage(result, 'Could not start backup'))
    else openTaskDetailPage(router, result.task_id)
  } catch (caught) {
    toast.error(errorMessage(caught, 'Could not start backup'))
  }
}

const menuOptions = computed(() => [
  ...(isMobile.value
    ? [
        { label: 'View analytics', icon: 'lucide-chart-line', onClick: goToAnalytics },
        { label: 'Install app', icon: 'lucide-plus', onClick: goToMarketplace },
      ]
    : []),
  { label: 'Login as admin', icon: 'lucide-log-in', onClick: loginAsAdmin },
  { label: 'Back up now', icon: 'lucide-archive', onClick: backupNow },
  {
    label: 'View jobs',
    icon: 'lucide-list-checks',
    onClick: () => router.push({ name: 'Tasks', query: { site: site.value?.name } }),
  },
])

// Provisioning and a pending setup wizard both resolve without us: poll quietly
// until they do, so the badge and the header button settle on their own.
const POLL_INTERVAL_MS = 5000
const isSettling = computed(
  () =>
    status.value === 'provisioning' || (status.value === 'online' && !site.value?.setup_complete),
)

let poll: ReturnType<typeof setInterval> | null = null
watch(
  isSettling,
  (settling) => {
    if (settling && !poll) {
      poll = setInterval(reload, POLL_INTERVAL_MS)
    } else if (!settling && poll) {
      clearInterval(poll)
      poll = null
    }
  },
  { immediate: true },
)

watch(
  () => site.value?.setup_complete,
  (complete, wasComplete) => {
    if (complete && wasComplete === false) toast.success('Site setup is complete')
  },
)

onUnmounted(() => {
  if (poll) clearInterval(poll)
})

onMounted(() => {
  load()
  loadStorage(true)
})
</script>

<template>
  <template v-if="loading">
    <PageHero>
      <template #icon><Skeleton class="rounded-6 size-9 sm:size-10 shrink-0" /></template>
      <template #title>
        <Skeleton class="rounded-4 w-40 h-4" />
        <Skeleton class="rounded-full w-14 h-5 shrink-0" />
      </template>

      <template #actions>
        <Skeleton class="hidden sm:block rounded-4 w-28 h-7" />
        <Skeleton class="hidden sm:block rounded-4 w-24 h-7" />
        <Skeleton class="rounded-4 size-7" />
      </template>
    </PageHero>

    <div class="px-3 md:px-4 pb-3 md:pb-4 mx-auto w-full max-w-3xl">
      <StickyToolbar>
        <Skeleton class="rounded-4 w-64 h-7 sm:h-8" />
      </StickyToolbar>
    </div>
  </template>

  <ErrorMessage v-else-if="error" class="px-3 md:px-4 py-12" :message="error" />

  <template v-else-if="site">
    <PageHero icon="lucide-globe">
      <template #title>
        <h1 class="text-lg-medium truncate">
          {{ site.name }}
        </h1>

        <Badge :label="statusLabel" :theme="statusBadgeTheme" class="shrink-0" />
      </template>

      <template v-if="storageUsed" #subtitle>{{ storageUsed }} used</template>
      <template #actions>
        <Button variant="ghost" class="hidden sm:flex" @click="goToAnalytics">
          <template #prefix><span class="size-4 lucide-chart-line" /></template>
          View analytics
        </Button>

        <Button class="hidden sm:flex" @click="goToMarketplace">
          <template #prefix><span class="size-4 lucide-plus" /></template>
          Install app
        </Button>

        <Dropdown :options="menuOptions">
          <template #default="{ open }">
            <Button
              :active="open"
              icon="lucide-ellipsis"
              label="Site actions"
              tooltip="Site actions"
            />
          </template>
        </Dropdown>
      </template>
    </PageHero>

    <div class="px-4 lg:px-0 pb-3 md:pb-4 mx-auto w-full max-w-3xl">
      <StickyToolbar>
        <TabButtons v-model="activeTab" :options="tabs" :size="isMobile ? 'md' : 'sm'" />
      </StickyToolbar>

      <SiteApps v-if="activeTab === 'apps'" :site-name="siteName" />
      <SiteBackups v-else-if="activeTab === 'backups'" :site-name="siteName" />
      <SiteConfig v-else-if="activeTab === 'config'" :site-name="siteName" />
      <Activities v-else-if="activeTab === 'activity'" :site-name="siteName" />
      <SiteSettings v-else-if="activeTab === 'settings'" :site-name="siteName" />
    </div>
  </template>

  <Teleport defer to="#header-actions">
    <Button
      :variant="site?.setup_complete ? 'subtle' : 'solid'"
      :loading="settingUpSite"
      @click="site?.setup_complete ? openSite() : setupSite()"
    >
      <template #prefix><span class="size-4 lucide-external-link" /></template>
      <span class="hidden sm:inline">{{ site?.setup_complete ? 'Open site' : 'Setup site' }}</span>
      <span class="sm:hidden">{{ site?.setup_complete ? 'Open' : 'Setup' }}</span>
    </Button>
  </Teleport>
</template>
