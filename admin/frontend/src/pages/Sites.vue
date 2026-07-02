<template>
  <UpdatesAvailableButton />

  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-center">
      <h1 class="font-medium text-ink-gray-8 text-base">
        Your sites <span class="font-normal text-ink-gray-5">({{ filteredSites.length }})</span>
      </h1>
    </div>

    <!-- Bar -->
    <div class="flex items-center gap-2 mt-4">
      <!-- Search text bar -->
      <FormControl v-model="search" type="text" placeholder="Search" class="flex-1">
        <template #prefix>
          <span class="size-4 text-ink-gray-5 lucide-search" />
        </template>
      </FormControl>
      <!-- Status filter -->
      <FormControl v-model="statusFilter" type="select" :options="statusOptions" class="max-w-24 sm:max-w-32" />
      <!-- List view type -->
      <TabButtons v-model="view" :options="viewOptions" class="hidden sm:block" />
    </div>

    <div v-if="loading" class="flex justify-center mt-16">
      <LoadingText />
    </div>
    <div v-else-if="error" class="mt-16">
      <ErrorMessage :message="error" />
    </div>

    <div v-else-if="filteredSites.length" class="mt-4">
      <!-- Grid view -->
      <div v-if="view === 'grid'" class="gap-3 grid grid-cols-1 md:grid-cols-2">
        <!-- Site Card -->
        <div v-for="site in filteredSites" :key="site.name"
          class="flex items-center gap-3 bg-surface-elevation-1 hover:bg-surface-gray-1 p-2 sm:p-4 border rounded-xl border-outline-gray-2 hover:border-outline-gray-3 transition-colors">
          <RouterLink :to="{ name: 'SiteDetail', params: { name: site.name } }"
            class="flex flex-1 items-center gap-3 min-w-0 no-underline">
            <!-- Icon -->
            <div class="place-items-center grid bg-surface-elevation-1 rounded-lg size-10 text-ink-gray-6 shrink-0">
              <span class="size-5 lucide-globe"></span>
            </div>
            <div class="flex-1 min-w-0">
              <!-- First Line -->
              <div class="gap-2 grid grid-cols-[3fr_1fr]">
                <div class="flex items-center gap-1.5 min-w-0">
                  <!-- Site Name -->
                  <span class="font-semibold text-ink-gray-9 text-base truncate">
                    {{ site.name }}
                  </span>

                  <!-- Status -->
                  <Badge :label="statusLabel(site)" :theme="statusTheme(site)" variant="subtle" size="sm"
                    class="shrink-0" />
                </div>

                <div class="flex justify-end">
                  <!-- Actions Dropdown -->
                  <Dropdown :options="siteMenuOptions(site)" placement="bottom-end">
                    <template #default="{ open }">
                      <Button variant="ghost" size="xs" class="!px-1.5">
                        <span class="size-4 lucide-more-horizontal" />
                      </Button>
                    </template>
                  </Dropdown>
                </div>
              </div>

              <!-- Second Line -->
              <p class="text-ink-gray-5 text-p-sm">
                {{ appsLabel(site) }}
              </p>
            </div>
          </RouterLink>
        </div>
      </div>

      <!-- List view -->
      <ListView v-else :columns="listColumns" :rows="listRows" row-key="name"
        :options="{ selectable: false, showTooltip: false }">
        <template #cell="{ column, row, item }">
          <div v-if="column.key === 'site'" class="flex items-center gap-3">
            <!-- Icon -->
            <div class="place-items-center grid bg-surface-elevation-1 rounded-lg size-10 text-ink-gray-6 shrink-0">
              <span class="size-5 lucide-globe" />
            </div>
            <RouterLink :to="{ name: 'SiteDetail', params: { name: row.site.name } }"
              class="font-medium text-ink-gray-9 text-sm no-underline truncate">
              {{ row.site.name }}
            </RouterLink>
          </div>
          <div v-else-if="column.key === 'status'">
            <Badge :label="statusLabel(row.site)" :theme="statusTheme(row.site)" variant="subtle" size="sm" />
          </div>
          <div v-else-if="column.key === 'apps'" class="text-ink-gray-6 text-sm">
            {{ item }}
          </div>
          <div v-else-if="column.key === 'actions'" class="flex justify-end">
            <Dropdown :options="siteMenuOptions(row.site)" placement="bottom-end">
              <template #default="{ open }">
                <Button variant="ghost" size="sm" :active="open">
                  <span class="size-4 lucide-more-vertical" />
                </Button>
              </template>
            </Dropdown>
          </div>
        </template>
      </ListView>
    </div>

    <!-- No s -->
    <p v-else class="mt-16 text-ink-gray-5 text-sm text-center">No sites found.</p>
  </div>

  <!-- New Site Button -->
  <Teleport defer to="#header-actions">
    <Button variant="solid" @click="showCreate = true">
      <template #prefix>
        <span class="size-4 lucide-plus" />
      </template>
      New site
    </Button>
  </Teleport>

  <NewSiteDialog v-model="showCreate" :sites="sites"
    @created="(taskId) => openTaskDetailPage(router, taskId)" />
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Badge,
  Button,
  Dropdown,
  ErrorMessage,
  FormControl,
  ListView,
  LoadingText,
  TabButtons,
  toast,
} from 'frappe-ui'
import NewSiteDialog from '@/components/NewSiteDialog.vue'
import UpdatesAvailableButton from '@/components/UpdatesAvailableButton.vue'
import { useBreadcrumbs } from '@/composables/useBreadcrumbs'
import { useSites } from '@/composables/useSites'
import { sitesApi } from '@/api/sites'
import { openTaskDetailPage } from '@/utils/taskRoute'

const router = useRouter()
const { setBreadcrumbs } = useBreadcrumbs()
const { sites, loading, error, load } = useSites()

setBreadcrumbs([{ label: 'Sites', route: { name: 'Sites' } }])

const search = ref('')
const statusFilter = ref('all')
const view = ref('grid')

const viewOptions = [
  { value: 'grid', icon: 'lucide-layout-grid' },
  { value: 'list', icon: 'lucide-list' },
]

const SITE_STATUS = {
  online: { label: 'Active', theme: 'green' },
  broken: { label: 'Broken', theme: 'red' },
  offline: { label: 'Paused', theme: 'orange' },
}

const statusOptions = [
  { label: 'Status', value: 'all' },
  { label: 'Active', value: 'online' },
  { label: 'Broken', value: 'broken' },
  { label: 'Paused', value: 'offline' },
]

function siteStatus(site) {
  if (!site.exists) return 'offline'
  if (site.broken) return 'broken'
  return 'online'
}

const statusLabel = (site) => SITE_STATUS[siteStatus(site)].label
const statusTheme = (site) => SITE_STATUS[siteStatus(site)].theme

function appsLabel(site) {
  const count = site.installed_apps?.length || 0
  return count === 1 ? '1 app' : `${count} apps`
}

const filteredSites = computed(() => {
  const query = search.value.toLowerCase().trim()
  return sites.value.filter((site) => {
    const matchesSearch = !query || site.name.toLowerCase().includes(query)
    const matchesStatus = statusFilter.value === 'all' || siteStatus(site) === statusFilter.value
    return matchesSearch && matchesStatus
  })
})

const listColumns = [
  { label: 'Site', key: 'site', align: 'left', width: 3 },
  { label: 'Status', key: 'status', align: 'left', width: 1.5 },
  { label: 'Apps', key: 'apps', align: 'left', width: 1.5 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const listRows = computed(() =>
  filteredSites.value.map((site) => ({
    name: site.name,
    site,
    apps: appsLabel(site),
  })),
)

async function loginAsAdmin(site) {
  const data = await sitesApi.login(site.name)
  if (data.url) window.open(data.url, '_blank')
  return data
}

function openSite(site) {
  toast.promise(loginAsAdmin(site), {
    loading: 'Logging in as admin…',
    success: 'Logged in as admin',
    error: 'Could not log in as admin',
  })
}

async function backupNow(site) {
  try {
    const result = await sitesApi.backups.create(site.name)
    if (result.ok) openTaskDetailPage(router, result.task_id)
    else toast.error(result.error || 'Could not start backup')
  } catch (caught) {
    toast.error(caught.message || 'Could not start backup')
  }
}

function siteMenuOptions(site) {
  return [
    { label: 'Open site', icon: 'lucide-external-link', onClick: () => openSite(site) },
    { label: 'Back up now', icon: 'lucide-archive', onClick: () => backupNow(site) },
  ]
}

const showCreate = ref(false)

onMounted(load)
</script>
