<template>
  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-start gap-4 pt-4 pb-2">
      <div class="flex flex-col items-start">
        <div class="flex items-center gap-2.5">
          <!-- !font-semibold: responsive text-* classes bake font-weight 420 and would override it -->
          <h1 class="!font-semibold text-ink-gray-9 text-2xl sm:text-3xl tracking-tight">
            Explore Frappe Marketplace
          </h1>
          <span
            v-if="benchVersionLabel"
            class="inline-flex items-center gap-1 bg-surface-gray-2 px-2 py-0.5 rounded-full h-min text-ink-gray-6 text-p-xs shrink-0"
          >
            <span class="size-3 lucide-box"></span> {{ benchVersionLabel }}
          </span>
        </div>
        <p class="mt-2 max-w-lg text-ink-gray-6 text-p-base">
          Open source apps built by developers worldwide for the Frappe ecosystem
        </p>
      </div>
      <button
        type="button"
        class="group inline-flex items-center bg-surface-gray-2 hover:bg-surface-gray-3 active:scale-[0.97] mt-1 px-2.5 py-1 rounded-full h-min text-ink-gray-7 text-p-sm shrink-0 transition duration-200 ease-[var(--ease-out)]"
        @click="showChooseSite = true"
      >
        <span
          class="size-3.5 text-ink-gray-5 mr-1.5"
          :class="currentSiteName ? 'lucide-globe' : 'lucide-layout-grid'"
        />
        {{ siteLabel }}
        <span
          class="size-3.5 text-ink-gray-5 max-w-0 ml-0 opacity-0 overflow-hidden group-hover:max-w-4 group-hover:ml-1 group-hover:opacity-100 transition-[max-width,margin,opacity] duration-200 ease-[var(--ease-out)] lucide-square-pen"
        />
      </button>
    </div>

    <!-- Filters -->
    <MarketplaceFilters
      v-model:search="search"
      v-model:pill="selectedPill"
      v-model:works-with="worksWith"
      :works-with-options="worksWithOptions"
      @add-from-github="showAddFromGithub = true"
    />

    <!-- Loading -->
    <div v-if="loading || error" class="flex flex-row justify-center items-center w-full h-[250px]">
      <LoadingText v-if="loading" class="mt-8" />
      <ErrorMessage v-else-if="error" :message="error" class="mt-8" />
    </div>

    <!-- Marketplace Apps -->

    <template v-else-if="isFiltered">
      <section v-if="filteredApps.length" class="mt-12">
        <p class="font-medium text-ink-gray-9 text-base">
          {{ filteredHeading }}
        </p>
        <div class="gap-x-6 gap-y-4 grid grid-cols-1 md:grid-cols-2 mt-3">
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
      <section v-if="otherBenchApps.length" class="mt-12">
        <p class="font-medium text-ink-gray-9 text-base">Your custom apps</p>
        <div class="gap-x-6 gap-y-4 grid grid-cols-1 md:grid-cols-2 mt-3">
          <MarketplaceAppCard
            v-for="app in otherBenchApps"
            :key="app.name"
            :app="app"
            @install="onInstall"
          />
        </div>
      </section>

      <section v-if="frappeApps.length" :class="otherBenchApps.length ? 'mt-10' : 'mt-12'">
        <p class="font-medium text-ink-gray-9 text-base">From Frappe</p>
        <div class="gap-x-6 gap-y-4 grid grid-cols-1 md:grid-cols-2 mt-3">
          <MarketplaceAppCard
            v-for="app in frappeApps"
            :key="app.name"
            :app="app"
            @install="onInstall"
          />
        </div>
      </section>

      <section v-if="communityApps.length" class="mt-10">
        <p class="font-medium text-ink-gray-9 text-base">Community</p>
        <div class="gap-x-6 gap-y-4 grid grid-cols-1 md:grid-cols-2 mt-3">
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
  <AddAppFromGithubDialog v-model:open="showAddFromGithub" />
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ErrorMessage, LoadingText } from 'frappe-ui'
import AddAppFromGithubDialog from '@/components/apps/AddAppFromGithubDialog.vue'
import ChooseSiteDialog from '@/components/sites/ChooseSiteDialog.vue'
import InstallAppDialog from '@/components/apps/InstallAppDialog.vue'
import MarketplaceAppCard from '@/components/marketplace/MarketplaceAppCard.vue'
import MarketplaceFilters from '@/components/marketplace/MarketplaceFilters.vue'
import { useMarketplace } from '@/composables/apps/useMarketplace'

const route = useRoute()

const {
  loading,
  error,
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

const filteredHeading = computed(() => {
  const name = selectedPill.value !== 'All' ? selectedPill.value : 'Matching apps'
  const count = filteredApps.value.length
  return `${name} · ${count} ${count === 1 ? 'app' : 'apps'}`
})

const showChooseSite = ref(false)
const showInstallApp = ref(false)
const showAddFromGithub = ref(false)
const installTarget = ref(null)

function onInstall(app) {
  installTarget.value = app
  showInstallApp.value = true
}

onMounted(load)
</script>
