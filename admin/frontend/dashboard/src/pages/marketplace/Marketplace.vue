<script setup lang="ts">
import { Button, ErrorMessage, Select, Skeleton, TabButtons, TextInput } from 'frappe-ui'
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AddAppFromGithubDialog from '@/components/apps/AddAppFromGithubDialog.vue'
import AppIcon from '@/components/apps/AppIcon.vue'
import InstallAppDialog from '@/components/apps/InstallAppDialog.vue'
import NewAppDialog from '@/components/apps/NewAppDialog.vue'
import GithubMark from '@/components/icons/GithubMark.vue'
import MarketplaceAppTile from '@/components/marketplace/MarketplaceAppTile.vue'
import type { MarketplaceListing } from '@/composables/apps/useMarketplace'
import { useMarketplace } from '@/composables/apps/useMarketplace'
import { PILLS, pillIcons } from '@/utils/marketplaceCategories'

const route = useRoute()
const router = useRouter()
const siteQuery = route.query.site
const initialSiteName = typeof siteQuery === 'string' ? siteQuery : ''

const {
  loading,
  error,
  search,
  selectedPill,
  worksWith,
  worksWithOptions,
  isFiltered,
  filteredApps,
  frappeApps,
  communityApps,
  load,
  sites,
  currentSiteName,
  otherBenchApps,
} = useMarketplace(initialSiteName)

const siteOptions = computed(() => [
  { label: 'All sites', value: '' },
  ...sites.value.map((site) => ({ label: site.name, value: site.name })),
])

const pillOptions = PILLS.map((pill) => ({ label: pill, value: pill, iconLeft: pillIcons[pill] }))

const isBrowsing = computed(() => !isFiltered.value && !search.value.trim())

const privateApps = computed(() => (isFiltered.value ? [] : otherBenchApps.value))

const sortBy = ref('stars')

const sortOptions = [
  { label: 'Most stars', value: 'stars' },
  { label: 'Name', value: 'name' },
]

const apps = computed(() => {
  const listed = isBrowsing.value
    ? [...frappeApps.value, ...communityApps.value]
    : filteredApps.value

  if (sortBy.value === 'name') return [...listed].sort((a, b) => a.title.localeCompare(b.title))

  return [...listed].sort((a, b) => (b.stars ?? -1) - (a.stars ?? -1))
})

const worksWithSelectOptions = computed(() => [
  { label: 'Any app', value: '', icon: 'lucide-blocks' },
  ...worksWithOptions.value.map((option) => ({
    label: option.title,
    value: option.name,
    icon: () =>
      h(AppIcon, { name: option.name, label: option.title, logo: option.logo_url, size: 'xs' }),
  })),
])

const showInstallApp = ref(false)
const showAddFromGithub = ref(false)
const showNewApp = ref(false)
const installTarget = ref<MarketplaceListing | null>(null)

watch(
  () => route.query.addFromGithub,
  (value) => {
    if (!value) return
    showAddFromGithub.value = true
    router.replace({ name: 'Marketplace', query: { ...route.query, addFromGithub: undefined } })
  },
  { immediate: true },
)

const onInstall = (app: MarketplaceListing) => {
  installTarget.value = app
  showInstallApp.value = true
}

const clearFilters = () => {
  search.value = ''
  selectedPill.value = 'All'
  worksWith.value = ''
}

onMounted(load)
</script>

<template>
  <Teleport defer to="#header-actions">
    <Button aria-label="Import app" @click="showAddFromGithub = true">
      <template #prefix><GithubMark class="size-4" /></template>
      <span class="hidden lg:inline">Import app</span>
    </Button>

    <Button variant="solid" aria-label="New app" @click="showNewApp = true">
      <template #prefix><span class="size-4 lucide-plus" /></template>
      <span class="hidden lg:inline">New app</span>
    </Button>
  </Teleport>

  <div class="flex flex-col gap-4 mx-auto px-3 md:px-8 pt-4 md:pt-8 pb-40 max-w-6xl">
    <header class="flex flex-wrap gap-2">
      <TextInput
        v-model="search"
        class="flex-1 basis-full md:basis-0 md:min-w-64"
        placeholder="Search apps"
      >
        <template #prefix><span class="size-4 text-ink-gray-5 lucide-search" /></template>
      </TextInput>

      <Select
        v-model="currentSiteName"
        :options="siteOptions"
        side="bottom"
        class="flex-1 md:flex-none md:w-48"
      >
        <template #prefix><span class="size-4 text-ink-gray-5 lucide-globe" /></template>
      </Select>

      <Select v-model="sortBy" :options="sortOptions" side="bottom" class="w-36">
        <template #prefix><span class="size-4 text-ink-gray-5 lucide-arrow-down-up" /></template>
      </Select>

      <Select v-model="worksWith" :options="worksWithSelectOptions" side="bottom" class="w-44" />
    </header>

    <TabButtons
      v-model="selectedPill"
      :options="pillOptions"
      variant="ghost"
      class="mb-2 overflow-x-auto"
    />

    <div v-if="loading" class="gap-4 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
      <Skeleton v-for="i in 9" :key="i" class="rounded-6 h-24" />
    </div>

    <ErrorMessage
      v-else-if="error"
      :message="error"
      class="flex justify-center items-center h-64"
    />

    <div
      v-else-if="!apps.length && !privateApps.length"
      class="flex flex-col items-center gap-3 py-20 border border-dashed rounded-7 border-outline-gray-2 text-center"
    >
      <span class="size-6 text-ink-gray-4 lucide-package-search" />

      <div>
        <p class="text-base-medium text-ink-gray-8">No apps match</p>

        <p class="mt-1 text-ink-gray-5 text-p-sm">
          Try another search, or import the app from GitHub.
        </p>
      </div>

      <Button label="Clear filters" @click="clearFilters" />
    </div>

    <template v-else>
      <section v-if="privateApps.length" class="mb-4">
        <h2 class="flex items-baseline gap-2 mb-3 text-2xl-semibold text-ink-gray-8">
          Private apps
          <span class="text-ink-gray-5 text-base">{{ privateApps.length }}</span>
        </h2>

        <div class="gap-4 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
          <MarketplaceAppTile
            v-for="app in privateApps"
            :key="app.name"
            :app="app"
            @install="onInstall"
          />
        </div>
      </section>

      <section v-if="apps.length">
        <h2 class="flex items-baseline gap-2 mb-3 text-2xl-semibold text-ink-gray-8">
          {{ selectedPill === 'All' ? 'All apps' : selectedPill }}
          <span class="text-ink-gray-5 text-base">{{ apps.length }}</span>
        </h2>

        <div class="gap-4 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
          <MarketplaceAppTile v-for="app in apps" :key="app.name" :app="app" @install="onInstall" />
        </div>
      </section>
    </template>
  </div>

  <InstallAppDialog
    v-model:open="showInstallApp"
    :app="installTarget"
    :sites="sites"
    :site-name="currentSiteName"
  />

  <AddAppFromGithubDialog v-model:open="showAddFromGithub" :site-name="currentSiteName" />

  <NewAppDialog v-model:open="showNewApp" />
</template>
