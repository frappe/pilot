<template>
  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex flex-col items-start pt-4 pb-2">
      <!-- !font-semibold: responsive text-* classes bake font-weight 420 and would override it -->
      <h1 class="!font-semibold text-ink-gray-9 text-2xl sm:text-3xl tracking-tight">Code editor</h1>
      <p class="mt-2 max-w-lg text-ink-gray-6 text-p-base">
        Browse and edit the files of an installed app in a full-screen editor.
      </p>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex justify-center items-center w-full h-[250px]">
      <LoadingText class="mt-8" />
    </div>

    <!-- Apps -->
    <section v-else-if="appObjects.length" class="mt-10">
      <p class="font-medium text-ink-gray-9 text-base">
        Installed apps · {{ appObjects.length }}
      </p>
      <div class="gap-x-6 gap-y-4 grid grid-cols-1 md:grid-cols-2 mt-3">
        <a
          v-for="app in appObjects"
          :key="app.name"
          :href="`/editor/${encodeURIComponent(app.name)}`"
          target="_blank"
          rel="noopener"
          class="group block bg-surface-white hover:bg-surface-gray-1 px-3 border border-outline-gray-2 hover:border-outline-gray-3 rounded-lg no-underline transition duration-150 ease-[var(--ease-out)]"
        >
          <MarketplaceAppCard :app="app">
            <template #actions>
              <span
                class="size-4 text-ink-gray-4 group-hover:text-ink-gray-6 shrink-0 transition-transform duration-150 ease-[var(--ease-out)] group-hover:translate-x-0.5 lucide-arrow-up-right" />
            </template>
          </MarketplaceAppCard>
        </a>
      </div>
    </section>

    <p v-else class="mt-8 text-ink-gray-5 text-sm text-center">No apps found in this bench.</p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { LoadingText } from 'frappe-ui'
import { appsApi } from '@/api/apps'
import MarketplaceAppCard from '@/components/marketplace/MarketplaceAppCard.vue'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'

const { titleMap, descriptionMap, logoMap, load: loadRegistry } = useAppRegistry()

const installed = ref([])
const loading = ref(true)

const appObjects = computed(() =>
  installed.value.map((app) => ({
    name: app.name,
    title: titleMap.value[app.name] || app.title || app.name,
    description: descriptionMap.value[app.name] || app.description || '',
    logo_url: logoMap.value[app.name] || null,
  })),
)

onMounted(async () => {
  loadRegistry()
  try {
    installed.value = await appsApi.installed()
  } catch {
    installed.value = []
  } finally {
    loading.value = false
  }
})
</script>
