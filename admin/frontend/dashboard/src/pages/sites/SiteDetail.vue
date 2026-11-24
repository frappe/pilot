<script setup lang="ts">
import {
  Badge,
  type BadgeProps,
  Button,
  Dropdown,
  ErrorMessage,
  TabList,
  Tabs,
  TabTrigger,
  toast,
} from 'frappe-ui'
import { computed, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import CloneDialog from '@/components/common/CloneDialog.vue'
import SiteAnalytics from '@/components/sites/Analytics.vue'
import SiteApps from '@/components/sites/Apps.vue'
import SiteBackups from '@/components/sites/Backups.vue'
import SiteConfig from '@/components/sites/Config.vue'
import SiteOverview from '@/components/sites/Overview.vue'
import SiteSettings from '@/components/sites/Settings.vue'
import Activities from '@/pages/Activities.vue'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { tasksApi } from '@/api/tasks'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useSession } from '@/composables/auth/useSession'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { useSite } from '@/composables/sites/useSite'
import { errorMessage } from '@/utils/error'
import { toSentenceCase } from '@/utils/format'
import { siteLabel } from '@/utils/taskFormat'
import { openTaskDetailPage } from '@/utils/taskRoute'

const route = useRoute()
const router = useRouter()
const siteName = Array.isArray(route.params.name) ? route.params.name[0] : route.params.name

const { setBreadcrumbs } = useBreadcrumbs()
const { site, error, status, load, reload, login, backup, apps, loadApps } = useSite(siteName)

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
  { value: 'overview', label: 'Overview', icon: 'lucide-layout-dashboard' },
  { value: 'analytics', label: 'Analytics', icon: 'lucide-chart-line' },
  { value: 'apps', label: 'Apps', icon: 'lucide-package' },
  { value: 'backups', label: 'Backups', icon: 'lucide-archive' },
  { value: 'config', label: 'Config', icon: 'lucide-braces' },
  { value: 'activity', label: 'Activity', icon: 'lucide-history' },
  { value: 'settings', label: 'Settings', icon: 'lucide-settings' },
]

const VALID_TABS = tabs.map((t) => t.value)
const tabParam = Array.isArray(route.params.tab) ? route.params.tab[0] : route.params.tab
const activeTab = ref(VALID_TABS.includes(tabParam) ? tabParam : 'overview')

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

const viewJobs = () => router.push({ name: 'Tasks', query: { site: siteName } })

const viewCreateJob = async () => {
  try {
    const tasks = await tasksApi.list()
    const job = tasks.find(
      (task) => ['queued', 'running'].includes(task.status) && siteLabel(task) === siteName,
    )

    if (job) openTaskDetailPage(router, job.task_id)
    else viewJobs()
  } catch (caught) {
    toast.error(errorMessage(caught, 'Could not load the site job'))
  }
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

const { session } = useSession()

const openInEditor = () =>
  window.open(`/editor/site/${encodeURIComponent(siteName)}`, '_blank', 'noopener')

const menuOptions = computed(() => [
  ...(isMobile.value
    ? [{ label: 'Install app', icon: 'lucide-plus', onClick: goToMarketplace }]
    : []),
  { label: 'Login as admin', icon: 'lucide-log-in', onClick: loginAsAdmin },
  ...(session.codeEditor
    ? [{ label: 'Open in code editor', icon: 'lucide-code', onClick: openInEditor }]
    : []),
  { label: 'Back up now', icon: 'lucide-archive', onClick: backupNow },
  { label: 'View jobs', icon: 'lucide-list-checks', onClick: viewJobs },
  { label: 'Clone site', icon: 'lucide-copy', onClick: () => (showClone.value = true) },
])

const showClone = ref(false)

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
})
</script>

<template>
  <ErrorMessage v-if="error" class="px-3 md:px-4 py-12" :message="error" />

  <template v-else>
    <Tabs v-model="activeTab" class="overflow-x-auto [scrollbar-width:none]">
      <TabList variant="underline" size="md" class="px-3 md:px-4 w-max min-w-full">
        <TabTrigger
          v-for="tab in tabs"
          :key="tab.value"
          :value="tab.value"
          :label="tab.label"
          :icon-left="tab.icon"
          class="py-1.5"
        />
      </TabList>
    </Tabs>

    <div class="p-3 md:p-4 pb-10 md:pb-10">
      <SiteOverview
        v-if="activeTab === 'overview'"
        :site-name="siteName"
        @open="activeTab = $event"
      />

      <SiteAnalytics v-else-if="activeTab === 'analytics'" :site-name="siteName" />
      <SiteApps v-else-if="activeTab === 'apps'" :site-name="siteName" />
      <SiteBackups v-else-if="activeTab === 'backups'" :site-name="siteName" />
      <SiteConfig v-else-if="activeTab === 'config'" :site-name="siteName" />
      <Activities v-else-if="activeTab === 'activity'" :site-name="siteName" />
      <SiteSettings v-else-if="activeTab === 'settings'" :site-name="siteName" />
    </div>
  </template>

  <Teleport defer to="#header-badge">
    <Badge v-if="site" :label="statusLabel" :theme="statusBadgeTheme" />
  </Teleport>

  <Teleport defer to="#header-actions">
    <Button v-if="site && status === 'provisioning'" @click="viewCreateJob">
      <template #prefix><span class="size-4 lucide-list-checks" /></template>
      View job
    </Button>

    <Button v-if="site" class="hidden sm:flex" @click="goToMarketplace">
      <template #prefix><span class="size-4 lucide-plus" /></template>
      Install app
    </Button>

    <Dropdown v-if="site" :options="menuOptions">
      <template #default="{ open }">
        <Button :active="open" icon="lucide-ellipsis" label="Site actions" tooltip="Site actions" />
      </template>
    </Dropdown>

    <Button
      :variant="site?.setup_complete ? 'subtle' : 'solid'"
      :loading="settingUpSite"
      @click="site?.setup_complete ? openSite() : setupSite()"
    >
      <template #prefix><span class="size-4 lucide-external-link" /></template>
      <span class="hidden sm:inline">{{ site?.setup_complete ? 'Visit site' : 'Setup site' }}</span>
      <span class="sm:hidden">{{ site?.setup_complete ? 'Open' : 'Setup' }}</span>
    </Button>
  </Teleport>

  <CloneDialog
    v-model="showClone"
    kind="site"
    :source="siteName"
    @started="(taskId) => openTaskDetailPage(router, taskId)"
  />
</template>
