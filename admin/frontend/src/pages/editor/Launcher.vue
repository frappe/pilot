<template>
  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-center gap-3">
      <div>
        <h1 class="font-semibold text-ink-gray-9 text-xl">Code editor</h1>
        <p class="mt-1 text-ink-gray-5 text-p-sm sm:hidden">Edit installed app files.</p>
        <p class="mt-1 text-ink-gray-5 text-p-base hidden sm:block">Browse and edit the files of installed apps.</p>
      </div>
    </div>

    <!-- Disabled -->
    <p v-if="!session.developerMode" class="mt-16 text-ink-gray-5 text-sm text-center">
      Enable Developer mode in Settings to use the code editor.
    </p>

    <!-- Loading -->
    <div v-else-if="loading" class="flex justify-center mt-16">
      <LoadingText />
    </div>

    <!-- Apps -->
    <section v-else-if="appObjects.length" class="mt-6">
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
          class="group block bg-surface-white hover:bg-surface-gray-1 p-2.5 border border-outline-gray-2 hover:border-outline-gray-3 rounded-lg no-underline transition duration-150 ease-[var(--ease-out)]"
        >
          <MarketplaceAppCard :app="app" class="-my-2">
            <template #actions><span /></template>
          </MarketplaceAppCard>
        </a>
      </div>
    </section>

    <p v-else class="mt-16 text-ink-gray-5 text-sm text-center">No apps found in this bench.</p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { LoadingText } from 'frappe-ui'
import { appsApi } from '@/api/apps'
import MarketplaceAppCard from '@/components/marketplace/MarketplaceAppCard.vue'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useSession } from '@/composables/auth/useSession'

const { session } = useSession()
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
