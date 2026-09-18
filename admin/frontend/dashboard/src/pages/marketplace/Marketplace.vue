<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge, Button, ErrorMessage, Skeleton } from 'frappe-ui'

import AddAppFromGithubDialog from '@/components/apps/AddAppFromGithubDialog.vue'
import PageHero from '@/components/common/PageHero.vue'
import ChooseSiteDialog from '@/components/sites/ChooseSiteDialog.vue'
import InstallAppDialog from '@/components/apps/InstallAppDialog.vue'
import MarketplaceAppCard from '@/components/marketplace/MarketplaceAppCard.vue'
import MarketplaceAppCardSkeleton from '@/components/marketplace/MarketplaceAppCardSkeleton.vue'
import MarketplaceFilters from '@/components/marketplace/MarketplaceFilters.vue'

import { useMarketplace } from '@/composables/apps/useMarketplace'
import { useIsMobile } from '@/composables/common/useIsMobile'

const isMobile = useIsMobile()
const route = useRoute()
const router = useRouter()

const {
  loading,
  error,
  appCount,
  search,
  selectedPill,
  worksWith,
  worksWithOptions,
  isFiltered,
  filteredApps,
  benchVersionLabel,
  frappeApps,
  communityApps,
  load,
  sites,
  currentSiteName,
  otherBenchApps,
} = useMarketplace(route.query.site)

const siteLabel = computed(() => currentSiteName.value || 'All sites')

const appCountLabel = computed(
  () => `${appCount.value} ${appCount.value === 1 ? 'app' : 'apps'} available`,
)

const filteredHeading = computed(() => {
  const name = selectedPill.value !== 'All' ? selectedPill.value : 'Matching apps'
  const count = filteredApps.value.length
  return `${name} · ${count} ${count === 1 ? 'app' : 'apps'}`
})

const showChooseSite = ref(false)
const showInstallApp = ref(false)
const showAddFromGithub = ref(false)
const installTarget = ref(null)

watch(
  () => route.query.addFromGithub,
  (value) => {
    if (!value) return
    showAddFromGithub.value = true
    router.replace({ name: 'Marketplace', query: { ...route.query, addFromGithub: undefined } })
  },
  { immediate: true },
)

const onInstall = (app) => {
  installTarget.value = app
  showInstallApp.value = true
}

onMounted(load)
</script>

<template>
  <PageHero v-if="loading">
    <template #icon><Skeleton class="rounded-6 size-9 sm:size-10 shrink-0" /></template>
    <template #title>
      <Skeleton class="rounded-4 w-40 h-4" />
      <Skeleton class="rounded-full w-14 h-5 shrink-0" />
    </template>

    <template #subtitle><Skeleton class="rounded-4 w-24 h-3.5" /></template>
    <template #actions><Skeleton class="rounded-4 w-28 h-8 sm:h-7" /></template>
  </PageHero>

  <PageHero v-else icon="lucide-store">
    <template #title>
      <h1 class="text-lg-medium truncate">Frappe Marketplace</h1>
      <Badge v-if="benchVersionLabel" :label="benchVersionLabel" class="shrink-0">
        <template #prefix><span class="size-2.5 lucide-box" /></template>
      </Badge>
    </template>

    <template #subtitle>{{ error ? '' : appCountLabel }}</template>
    <template #actions>
      <Button
        class="[&>.truncate]:flex-1 [&>.truncate]:text-left text-base max-w-[180px] sm:max-w-[250px] overflow-hidden"
        :size="isMobile ? 'md' : 'sm'"
        @click="showChooseSite = true"
      >
        <template #prefix>
          <span class="size-4 text-ink-gray-5 lucide-globe" />
        </template>
        {{ siteLabel }}
        <template #suffix>
          <span class="size-4 text-ink-gray-5 lucide-chevron-down" />
        </template>
      </Button>
    </template>
  </PageHero>

  <MarketplaceFilters
    v-model:search="search"
    v-model:pill="selectedPill"
    v-model:works-with="worksWith"
    :works-with-options="worksWithOptions"
    @add-from-github="showAddFromGithub = true"
  />

  <div class="px-3 sm:px-4 mx-auto box-content max-w-3xl pb-40">
    <section v-if="loading" class="mt-12">
      <div class="flex items-center h-4">
        <Skeleton class="rounded-4 w-32 h-3.5" />
      </div>

      <div class="gap-x-6 gap-y-4 grid md:grid-cols-2 mt-3">
        <MarketplaceAppCardSkeleton v-for="i in 8" :key="i" :index="i - 1" />
      </div>
    </section>

    <div v-else-if="error" class="flex justify-center items-center w-full h-[250px]">
      <ErrorMessage :message="error" />
    </div>

    <template v-else-if="isFiltered">
      <section v-if="filteredApps.length" class="mt-12">
        <h2 class="font-medium">
          {{ filteredHeading }}
        </h2>

        <div class="gap-x-6 gap-y-4 grid md:grid-cols-2 mt-3">
          <MarketplaceAppCard
            v-for="app in filteredApps"
            :key="app.name"
            :app="app"
            @install="onInstall"
          />
        </div>
      </section>

      <p v-else class="mt-8 text-ink-gray-5 text-sm text-center">No apps found.</p>
    </template>

    <template v-else>
      <section v-if="otherBenchApps.length" class="mt-4">
        <h2 class="font-medium">Your custom apps</h2>
        <div class="gap-x-6 gap-y-4 grid md:grid-cols-2 mt-3">
          <MarketplaceAppCard
            v-for="app in otherBenchApps"
            :key="app.name"
            :app="app"
            @install="onInstall"
          />
        </div>
      </section>

      <section v-if="frappeApps.length" :class="otherBenchApps.length ? 'mt-10' : 'mt-12'">
        <h2 class="font-medium">From Frappe</h2>
        <div class="gap-x-6 gap-y-4 grid md:grid-cols-2 mt-3">
          <MarketplaceAppCard
            v-for="app in frappeApps"
            :key="app.name"
            :app="app"
            @install="onInstall"
          />
        </div>
      </section>

      <section v-if="communityApps.length" class="mt-10">
        <h2 class="font-medium">Community</h2>
        <div class="gap-x-6 gap-y-4 grid md:grid-cols-2 mt-3">
          <MarketplaceAppCard
            v-for="app in communityApps"
            :key="app.name"
            :app="app"
            @install="onInstall"
          />
        </div>
      </section>

      <p
        v-if="!frappeApps.length && !communityApps.length && !otherBenchApps.length"
        class="mt-8 text-ink-gray-5 text-sm text-center"
      >
        No apps found.
      </p>
    </template>
  </div>

  <ChooseSiteDialog v-model:open="showChooseSite" v-model:site="currentSiteName" :sites="sites" />
  <InstallAppDialog
    v-model:open="showInstallApp"
    :app="installTarget"
    :sites="sites"
    :site-name="currentSiteName"
  />
  <AddAppFromGithubDialog v-model:open="showAddFromGithub" :site-name="currentSiteName" />
</template>
