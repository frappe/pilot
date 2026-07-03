<template>
  <UpdatesAvailableButton />

  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-center">
      <div class="flex flex-col items-start">
        <h1 class="font-semibold text-ink-gray-9 text-base sm:text-xl">Marketplace</h1>
        <p class="sm:hidden mt-1 text-ink-gray-5 text-p-sm">
          Apps built by developers worldwide, ready to install.
        </p>
        <p class="hidden sm:block mt-1 text-ink-gray-5 text-p-base">
          Apps built by developers worldwide, ready to install on {{ benchName }}'s sites.
        </p>
      </div>
      <div class="h-min" v-if="benchVersionLabel">
        <span data-state="closed" data-grace-area-trigger=""
          class="inline-flex items-center gap-1.5 bg-surface-gray-1 mt-0.5 px-2.5 py-1 border rounded-full border-outline-gray-2 h-min text-ink-gray-6 text-p-sm shrink-0">
          <span class="size-3.5 lucide-box"></span> {{ benchVersionLabel }}
        </span>
      </div>
    </div>

    <!-- Install target -->
    <div v-if="currentSiteName"
      class="flex justify-between items-center gap-3 bg-surface-gray-1 mt-6 px-3 py-2 border rounded-lg border-outline-gray-2">
      <div class="flex items-center gap-2 min-w-0 text-ink-gray-6 text-p-sm">
        <span
          class="place-items-center grid bg-surface-elevation-1 border rounded-md border-outline-gray-2 size-7 text-ink-gray-6 shrink-0"><span
            class="size-3.5 lucide-globe"></span></span>
        <span class="text-ink-gray-7 truncate">
          Installing onto <span class="font-medium text-ink-gray-9">{{ currentSiteName }}</span>
        </span>
      </div>
      <button class="ml-auto text-ink-gray-5 text-sm hover:underline underline-offset-2"
        @click="showChooseSite = true">Choose a different
        site</button>

    </div>

    <!-- Search -->

    <div class="gap-x-3 grid grid-cols-[8fr_2fr] mt-6">
      <FormControl v-model="search" type="text" placeholder="Search for any app">
        <template #prefix>
          <LucideSearch class="size-4 text-ink-gray-5" />
        </template>
      </FormControl>
      <FormControl v-model="selectedCategory" class="flex-" type="select" :options="categories" />
    </div>

    <!-- Loading -->
    <div v-if="loading || error" class="flex flex-row justify-center items-center w-full h-[250px]">
      <LoadingText v-if="loading" class="mt-8" />
      <ErrorMessage v-else-if="error" :message="error" class="mt-8" />
    </div>

    <!-- Marketplace Apps -->

    <template v-else>
      <section v-if="otherBenchApps.length" class="mt-6">
        <div class="flex justify-between items-center">
          <p class="font-semibold text-ink-gray-9 text-base">Custom Apps</p>
          <Button variant="subtle" size="sm" @click="showAddFromGithub = true">Add from GitHub</Button>
        </div>
        <div class="gap-x-10 grid grid-cols-1 md:grid-cols-2 mt-2">
          <MarketplaceAppCard v-for="app in otherBenchApps" :key="app.name" :app="app" @install="onInstall" />
        </div>
      </section>

      <section v-if="frappeApps.length" :class="otherBenchApps.length ? 'mt-8' : 'mt-6'">
        <p class="font-semibold text-ink-gray-9 text-base">From Frappe</p>
        <div class="gap-x-10 grid grid-cols-1 md:grid-cols-2 mt-2">
          <MarketplaceAppCard v-for="app in frappeApps" :key="app.name" :app="app" @install="onInstall" />
        </div>
      </section>

      <section v-if="communityApps.length" class="mt-8">
        <p class="font-semibold text-ink-gray-9 text-base">Community</p>
        <div class="gap-x-10 grid grid-cols-1 md:grid-cols-2 mt-2">
          <MarketplaceAppCard v-for="app in communityApps" :key="app.name" :app="app" @install="onInstall" />
        </div>
      </section>

      <p v-if="!frappeApps.length && !communityApps.length && !otherBenchApps.length"
        class="mt-8 text-ink-gray-5 text-sm text-center">
        No apps found.
      </p>

      <button type="button" class="block mt-6 text-ink-gray-5 text-sm text-left hover:underline underline-offset-2"
        @click="showAddFromGithub = true">
        Building your own? Install from GitHub
      </button>
    </template>
  </div>

  <ChooseSiteDialog v-model:open="showChooseSite" v-model:site="currentSiteName" :sites="sites" />
  <InstallAppDialog v-model:open="showInstallApp" :app="installTarget" :sites="sites" :site-name="currentSiteName" />
  <AddAppFromGithubDialog v-model:open="showAddFromGithub" />
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Button, ErrorMessage, FormControl, LoadingText } from 'frappe-ui'
import LucideSearch from '~icons/lucide/search'
import AddAppFromGithubDialog from '@/components/AddAppFromGithubDialog.vue'
import ChooseSiteDialog from '@/components/ChooseSiteDialog.vue'
import InstallAppDialog from '@/components/InstallAppDialog.vue'
import MarketplaceAppCard from '@/components/MarketplaceAppCard.vue'
import UpdatesAvailableButton from '@/components/UpdatesAvailableButton.vue'
import { useMarketplace } from '@/composables/useMarketplace'

const route = useRoute()

const {
  loading,
  error,
  search,
  selectedCategory,
  categories,
  benchName,
  benchVersion,
  benchVersionLabel,
  frappeApps,
  communityApps,
  load,
  sites,
  currentSiteName,
  otherBenchApps,
} = useMarketplace(route.query.site)

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
