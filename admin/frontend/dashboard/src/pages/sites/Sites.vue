<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, onMounted, ref, watch } from 'vue'

import { Badge, Button, Dropdown, ErrorMessage, Select, TabButtons, TextInput, toast } from 'frappe-ui'

import EmptyState from '@/components/common/EmptyState.vue'
import Table from '@/components/common/Table.vue'
import SiteSkeleton from '@/components/sites/SiteSkeleton.vue'
import NewSiteDialog from '@/components/sites/NewSiteDialog.vue'
import StickyToolbar from '@/components/common/StickyToolbar.vue'

import { sitesApi } from '@/api/sites'
import { apiErrorMessage } from '@/api/client'
import { openSiteLogin } from '@/utils/siteLogin'
import { openTaskDetailPage } from '@/utils/taskRoute'
import { useSites } from '@/composables/sites/useSites'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { useSiteStorage } from '@/composables/sites/useSiteStorage'

const route = useRoute()
const router = useRouter()
const isMobile = useIsMobile()
const { sites, loading, error, load } = useSites()
const { load: loadStorage, storageLabel } = useSiteStorage()

const search = ref('')
const statusFilter = ref('all')
const view = ref('grid')

const viewOptions = [
  { value: 'grid', icon: 'lucide-layout-grid' },
  { value: 'list', icon: 'lucide-list' },
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

const siteStatus = (site) => {
  // Provisioning wins over "offline": the site dir/site_config.json may not
  // exist yet in the earliest moments of a new-site/reinstall task.
  if (site.provisioning) return 'provisioning'
  if (!site.exists) return 'offline'
  if (site.broken) return 'broken'
  return 'online'
}

const statusInfo = (site) => SITE_STATUS[siteStatus(site)]

const appsLabel = (site) => {
  const count = site.active_apps?.length || 0
  return count === 1 ? '1 app' : `${count} apps`
}

// Storage lands after the list, so a card shows its app count alone until then.
const metaLabel = (site) => {
  const used = storageLabel(site.name)
  return used ? `${used} · ${appsLabel(site)}` : appsLabel(site)
}

const isFiltered = computed(() => Boolean(search.value.trim()) || statusFilter.value !== 'all')

const filteredSites = computed(() => {
  const query = search.value.toLowerCase().trim()
  return sites.value.filter((site) => {
    const matchesSearch = !query || site.name.toLowerCase().includes(query)
    const matchesStatus = statusFilter.value === 'all' || siteStatus(site) === statusFilter.value
    return matchesSearch && matchesStatus
  })
})

const hasCount = computed(() => !loading.value || sites.value.length > 0)

const listColumns = [
  { label: 'Site', key: 'site', class: 'w-1/2' },
  { label: 'Status', key: 'status' },
  { label: 'Storage', key: 'storage', class: 'text-ink-gray-6 text-sm' },
  { label: 'Apps', key: 'apps', class: 'text-ink-gray-6 text-sm' },
  { label: '', key: 'actions' },
]

const listRows = computed(() =>
  filteredSites.value.map((site) => ({
    id: site.name,
    site,
    storage: storageLabel(site.name),
    apps: appsLabel(site),
  })),
)

const loginAsAdmin = async (site) => {
  return openSiteLogin(() => sitesApi.loginLink(site.name), {
    onHint: (hint) => toast.info(hint),
  })
}

const openSite = (site) => {
  toast.promise(loginAsAdmin(site), {
    loading: 'Logging in as admin',
    success: 'Logged in as admin',
    error: (caught) => caught?.message || 'Could not log in as admin',
  })
}

const backupNow = async (site) => {
  try {
    const result = await sitesApi.backups.create(site.name)
    if (result.ok) openTaskDetailPage(router, result.task_id)
    else toast.error(apiErrorMessage(result, 'Could not start backup'))
  } catch (caught) {
    toast.error(caught.message || 'Could not start backup')
  }
}

const siteMenuOptions = (site) => {
  return [
    { label: 'Open site', icon: 'lucide-external-link', onClick: () => openSite(site) },
    { label: 'Back up now', icon: 'lucide-archive', onClick: () => backupNow(site) },
    {
      label: 'View analytics',
      icon: 'lucide-chart-line',
      onClick: () => router.push({ name: 'Analytics', query: { view: 'site', site: site.name } }),
    },
    {
      label: 'View jobs',
      icon: 'lucide-list-checks',
      onClick: () => router.push({ name: 'Tasks', query: { site: site.name } }),
    },
  ]
}

const showCreate = ref(false)

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
  <div class="p-3 md:p-4 mx-auto max-w-3xl">
    <StickyToolbar v-if="sites.length > 10" class="flex items-center gap-2">
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
        v-model="statusFilter"
        :options="statusOptions"
        :size="isMobile ? 'md' : 'sm'"
        class="max-w-24 sm:max-w-32"
      />
      <TabButtons
        v-model="view"
        :options="viewOptions"
        :size="isMobile ? 'md' : 'sm'"
        class="hidden sm:block"
      />
    </StickyToolbar>

    <ErrorMessage v-if="error" class="mt-16" :message="error" />

    <template v-else-if="loading || filteredSites.length">
      <div
        v-if="loading || view === 'grid'"
        class="gap-3 grid mt-1"
        :class="loading || filteredSites.length > 1 ? 'md:grid-cols-2' : ''"
      >
        <template v-if="loading">
          <SiteSkeleton v-for="index in 4" :key="index" :index="index - 1" />
        </template>

        <!-- Site Card -->
        <router-link
          v-for="site in filteredSites"
          :key="site.name"
          class="flex items-center gap-3 bg-surface-base p-2 px-3 border rounded-6 border-outline-gray-2 hover:border-outline-gray-4 transition-colors"
          :to="{ name: 'SiteDetail', params: { name: site.name } }"
        >
          <div class="bg-surface-gray-2 flex rounded-4 p-2 text-ink-gray-6 shrink-0">
            <span class="size-4 lucide-globe m-auto" />
          </div>

          <div class="flex flex-1 flex-wrap items-center gap-x-1.5 min-w-0">
            <span class="flex-1 max-w-fit font-medium truncate">
              {{ site.name }}
            </span>

            <span
              v-if="siteStatus(site) !== 'online'"
              class="flex items-center gap-1.5 mx-1 text-ink-gray-6 text-p-sm"
            >
              <span class="rounded-full size-1.5" :class="statusInfo(site).dot" />
              {{ statusInfo(site).label }}
            </span>

            <Dropdown :options="siteMenuOptions(site)">
              <Button
                variant="ghost"
                size="xs"
                icon="lucide-ellipsis"
                label="Site actions"
                tooltip="Actions"
                class="ml-auto"
              />
            </Dropdown>

            <p class="w-full text-ink-gray-5 text-p-sm">
              {{ metaLabel(site) }}
            </p>
          </div>
        </router-link>
      </div>

      <Table v-else :columns="listColumns" :rows="listRows" height="h-auto" class="mt-1">
        <template #site="{ row }">
          <router-link
            :to="{ name: 'SiteDetail', params: { name: row.site.name } }"
            class="font-medium"
          >
            {{ row.site.name }}
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
</template>
