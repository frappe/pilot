<script setup lang="ts">
import {
  Badge,
  Button,
  Dropdown,
  ErrorMessage,
  Select,
  TabButtons,
  TextInput,
  toast,
} from 'frappe-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage, hasApiError } from '@/api/client'
import { sitesApi } from '@/api/sites'
import CloneDialog from '@/components/common/CloneDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import Sparkline from '@/components/common/Sparkline.vue'
import StickyToolbar from '@/components/common/StickyToolbar.vue'
import Table from '@/components/common/Table.vue'
import NewSiteDialog from '@/components/sites/NewSiteDialog.vue'
import SiteSkeleton from '@/components/sites/SiteSkeleton.vue'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { useSiteStorage } from '@/composables/sites/useSiteStorage'
import { useSites } from '@/composables/sites/useSites'
import type { SiteResource } from '@/types/sites'
import { errorMessage } from '@/utils/error'
import { openSiteLink } from '@/utils/siteLogin'
import { openTaskDetailPage } from '@/utils/taskRoute'

const route = useRoute()
const router = useRouter()
const isMobile = useIsMobile()
const { sites, loading, error, load } = useSites()
const { load: loadStorage, storageLabel, storageBytes } = useSiteStorage()

const search = ref('')
const statusFilter = ref('all')
const sortBy = ref('name')
const view = ref('grid')

const viewOptions = [
  { value: 'grid', label: 'Grid view', icon: 'lucide-layout-grid' },
  { value: 'list', label: 'List view', icon: 'lucide-list' },
]

const SITE_STATUS = {
  online: { label: 'Active', dot: 'bg-surface-green-8' },
  broken: { label: 'Broken', dot: 'bg-surface-red-8' },
  offline: { label: 'Paused', dot: 'bg-surface-orange-8' },
  provisioning: { label: 'Creating', dot: 'bg-surface-blue-8' },
}

const statusOptions = [
  { label: 'Status', value: 'all' },
  { label: 'Active', value: 'online' },
  { label: 'Broken', value: 'broken' },
  { label: 'Paused', value: 'offline' },
  { label: 'Creating', value: 'provisioning' },
]

const sortOptions = [
  { label: 'Name', value: 'name', icon: 'lucide-arrow-down-a-z' },
  { label: 'Largest', value: 'largest', icon: 'lucide-hard-drive' },
  { label: 'Most requests', value: 'requests', icon: 'lucide-chart-line' },
]

const siteStatus = (site: SiteResource) => {
  // Provisioning wins over "offline": the site dir/site_config.json may not
  // exist yet in the earliest moments of a new-site/reinstall task.
  if (site.provisioning) return 'provisioning'
  if (!site.exists) return 'offline'
  if (site.broken) return 'broken'
  return 'online'
}

const statusInfo = (site: SiteResource) => SITE_STATUS[siteStatus(site)]

const appsLabel = (site: SiteResource) => {
  const count = site.active_apps?.length || 0
  return count === 1 ? '1 app' : `${count} apps`
}

const requestTotal = (site: SiteResource) =>
  (site.requests || []).reduce((total, count) => total + count, 0)

const uptimeLabel = (site: SiteResource) =>
  typeof site.uptime_percent === 'number' ? `${Number(site.uptime_percent.toFixed(1))}%` : ''

const compactNumber = new Intl.NumberFormat(undefined, { notation: 'compact' })

const siteMetrics = (site: SiteResource) =>
  [
    { icon: 'lucide-hard-drive', value: storageLabel(site.name) },
    { icon: 'lucide-package', value: appsLabel(site) },
  ].filter((metric) => metric.value)

const isFiltered = computed(() => Boolean(search.value.trim()) || statusFilter.value !== 'all')

const filteredSites = computed(() => {
  const query = search.value.toLowerCase().trim()
  const matches = sites.value.filter((site) => {
    const matchesSearch =
      !query ||
      [site.name, site.primary_domain].some((label) => label.toLowerCase().includes(query))
    const matchesStatus = statusFilter.value === 'all' || siteStatus(site) === statusFilter.value
    return matchesSearch && matchesStatus
  })

  if (sortBy.value === 'largest') {
    return matches.sort((a, b) => storageBytes(b.name) - storageBytes(a.name))
  }

  if (sortBy.value === 'requests') return matches.sort((a, b) => requestTotal(b) - requestTotal(a))
  return matches.sort((a, b) => a.primary_domain.localeCompare(b.primary_domain))
})

const hasCount = computed(() => !loading.value || sites.value.length > 0)
const showSkeleton = computed(() => loading.value && sites.value.length === 0)

const listColumns = [
  { label: 'Site', key: 'site', class: 'w-1/3' },
  { label: 'Status', key: 'status' },
  { label: 'Requests · 24h', key: 'requests', class: 'text-ink-gray-6 text-sm tabular-nums' },
  { label: 'Uptime', key: 'uptime', class: 'text-ink-gray-6 text-sm tabular-nums' },
  { label: 'Storage', key: 'storage', class: 'text-ink-gray-6 text-sm tabular-nums' },
  { label: 'Apps', key: 'apps', class: 'text-ink-gray-6 text-sm' },
  { label: '', key: 'actions' },
]

const listRows = computed(() =>
  filteredSites.value.map((site) => ({
    id: site.name,
    site,
    storage: storageLabel(site.name),
    apps: appsLabel(site),
    requests: requestTotal(site) ? requestTotal(site).toLocaleString() : '',
    uptime: uptimeLabel(site),
  })),
)

const loginAsAdmin = async (site: SiteResource) => {
  return openSiteLink(() => sitesApi.loginLink(site.name), {
    onHint: (hint) => toast.info(hint),
  })
}

const openSite = async (site: SiteResource) => {
  try {
    await openSiteLink(async () => {
      const detail = await sitesApi.detail(site.name)
      return { url: `${detail.url}/desk` }
    })
  } catch (caught) {
    toast.error(errorMessage(caught, 'Could not open site'))
  }
}

const openAsAdmin = (site: SiteResource) => {
  toast.promise(loginAsAdmin(site), {
    loading: 'Logging in as admin',
    success: 'Logged in as admin',
    error: (caught: unknown) => errorMessage(caught, 'Could not log in as admin'),
  })
}

const backupNow = async (site: SiteResource) => {
  try {
    const result = await sitesApi.backups.create(site.name)
    if (hasApiError(result)) toast.error(apiErrorMessage(result, 'Could not start backup'))
    else openTaskDetailPage(router, result.task_id)
  } catch (caught) {
    toast.error(errorMessage(caught, 'Could not start backup'))
  }
}

const siteMenuOptions = (site: SiteResource) => {
  return [
    {
      label: 'Clone site',
      icon: 'lucide-copy',
      onClick: () => {
        cloneSource.value = site.name
        showClone.value = true
      },
    },
    { label: 'Visit site', icon: 'lucide-external-link', onClick: () => openSite(site) },
    { label: 'Login as admin', icon: 'lucide-log-in', onClick: () => openAsAdmin(site) },
    { label: 'Back up now', icon: 'lucide-archive', onClick: () => backupNow(site) },
    {
      label: 'View analytics',
      icon: 'lucide-chart-line',
      onClick: () =>
        router.push({
          name: 'Analytics',
          query: { view: 'site', site: site.name },
        }),
    },
    {
      label: 'View jobs',
      icon: 'lucide-list-checks',
      onClick: () => router.push({ name: 'Tasks', query: { site: site.name } }),
    },
  ]
}

const showCreate = ref(false)
const showClone = ref(false)
const cloneSource = ref('')

watch(
  () => route.query.new,
  (value) => {
    if (!value) return
    showCreate.value = true
    router.replace({ name: 'Sites' })
  },
  { immediate: true },
)

onMounted(() => {
  load()
  loadStorage(true)
})
</script>

<template>
  <div class="px-3 md:px-4 pb-4">
    <StickyToolbar class="flex items-center gap-2">
      <TextInput
        v-model="search"
        placeholder="Search"
        :size="isMobile ? 'md' : 'sm'"
        class="flex-1"
      >
        <template #prefix>
          <span class="size-4 text-ink-gray-5 lucide-search" />
        </template>
      </TextInput>

      <Select
        side="bottom"
        v-model="statusFilter"
        :options="statusOptions"
        :size="isMobile ? 'md' : 'sm'"
        class="max-w-24 sm:max-w-32"
      >
        <template #item-prefix="{ item }">
          <span
            v-if="item.value !== 'all'"
            class="rounded-full size-1.5 shrink-0"
            :class="SITE_STATUS[item.value as keyof typeof SITE_STATUS].dot"
          />
        </template>
      </Select>

      <Select
        side="bottom"
        v-model="sortBy"
        :options="sortOptions"
        :size="isMobile ? 'md' : 'sm'"
        class="max-w-32 sm:max-w-40"
      />
      <TabButtons
        v-model="view"
        :options="viewOptions"
        :size="isMobile ? 'md' : 'sm'"
        class="hidden sm:block"
      />
    </StickyToolbar>

    <ErrorMessage v-if="error" class="mt-16" :message="error" />

    <template v-else-if="showSkeleton || filteredSites.length">
      <div
        v-if="showSkeleton || view === 'grid'"
        class="gap-4 grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      >
        <template v-if="showSkeleton">
          <SiteSkeleton v-for="index in 4" :key="index" :index="index - 1" />
        </template>

        <!-- card -->
        <router-link
          v-for="site in filteredSites"
          :key="site.name"
          class="flex flex-col gap-3 bg-surface-gray-0 px-4 pt-3.5 pb-4 border border-outline-gray-1.5 hover:border-outline-gray-4 rounded-6"
          :to="{ name: 'SiteDetail', params: { name: site.name } }"
        >
          <div class="flex items-center gap-2 text-ink-gray-5 text-sm">
            <span class="rounded-full size-2 shrink-0" :class="statusInfo(site).dot" />
            <span class="text-base-medium text-ink-gray-8 truncate">{{ site.primary_domain }}</span>
            <span v-if="site.ssl" class="size-3.5 text-ink-gray-4 lucide-lock shrink-0" />
            <span v-if="siteStatus(site) !== 'online'" class="ml-auto shrink-0">
              {{ statusInfo(site).label }}
            </span>

            <Dropdown :options="siteMenuOptions(site)">
              <template #default="{ open }">
                <Button
                  size="xs"
                  variant="ghost"
                  :active="open"
                  icon="lucide-ellipsis"
                  label="Site actions"
                  class="-mt-2 -mr-2"
                  :class="siteStatus(site) === 'online' && 'ml-auto'"
                  @click.prevent.stop
                />
              </template>
            </Dropdown>
          </div>

          <div
            class="flex items-center gap-3 text-ink-gray-5 text-sm tabular-nums whitespace-nowrap"
          >
            <span
              v-for="metric in siteMetrics(site)"
              :key="metric.icon"
              class="flex items-center gap-1.5"
            >
              <span class="size-3.5 text-ink-gray-4" :class="metric.icon" />
              {{ metric.value }}
            </span>
          </div>

          <div v-if="site.requests?.length" class="flex flex-col gap-3 mt-1">
            <Sparkline :values="site.requests || []" unit="request" class="w-full h-8" />

            <div class="flex justify-between text-ink-gray-5 text-xs tabular-nums">
              <span>
                <span class="text-ink-gray-8">{{ compactNumber.format(requestTotal(site)) }}</span>
                requests · 24h
              </span>

              <span v-if="uptimeLabel(site)" class="flex items-center gap-1">
                <span class="size-3 text-ink-gray-4 lucide-activity" />
                <span class="text-ink-gray-8">{{ uptimeLabel(site) }}</span>
                uptime
              </span>
            </div>
          </div>
        </router-link>
      </div>

      <Table v-else :columns="listColumns" :rows="listRows" height="h-auto">
        <template #site="{ row }">
          <router-link
            :to="{ name: 'SiteDetail', params: { name: row.site.name } }"
            class="font-medium"
          >
            {{ row.site.primary_domain }}
          </router-link>
        </template>

        <template #status="{ row }">
          <span
            v-if="statusInfo(row.site)"
            class="flex items-center gap-1.5 text-ink-gray-6 text-p-sm"
          >
            <span class="rounded-full size-1.5" :class="statusInfo(row.site).dot" />
            {{ statusInfo(row.site).label }}
          </span>
        </template>

        <template #actions="{ row }">
          <Dropdown :options="siteMenuOptions(row.site)">
            <template #default="{ open }">
              <Button
                variant="ghost"
                :active="open"
                icon="lucide-ellipsis"
                label="Site actions"
                tooltip="Actions"
              />
            </template>
          </Dropdown>
        </template>
      </Table>
    </template>

    <EmptyState
      v-else
      class="mt-4"
      icon="lucide-globe"
      :title="isFiltered ? 'No matching sites' : 'No sites yet'"
      :description="
        isFiltered
          ? 'No sites match your search or status filter.'
          : 'Create a site to get started on this bench.'
      "
    />
  </div>

  <Teleport v-if="hasCount" defer to="#header-badge">
    <Badge :label="filteredSites.length" />
  </Teleport>

  <Teleport defer to="#header-actions">
    <Button variant="solid" @click="showCreate = true">
      <template #prefix>
        <span class="size-4 lucide-plus" />
      </template>
      New site
    </Button>
  </Teleport>

  <NewSiteDialog
    v-model="showCreate"
    :sites="sites"
    @started="(taskId) => openTaskDetailPage(router, taskId)"
  />
  <CloneDialog
    v-model="showClone"
    kind="site"
    :source="cloneSource"
    @started="(taskId) => openTaskDetailPage(router, taskId)"
  />
</template>
