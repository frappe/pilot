<script setup lang="ts">
import { computed, KeepAlive, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Select, TabButtons } from 'frappe-ui'

import EmptyState from '@/components/common/EmptyState.vue'
import PythonConsole from '@/components/console/PythonConsole.vue'
import SQLConsole from '@/components/console/SQLConsole.vue'

import { databaseApi } from '@/api/database'
import type { DatabaseSite } from '@/types/database'

const route = useRoute()
const router = useRouter()

const sites = ref<DatabaseSite[]>([])
const selectedSite = ref(typeof route.query.site === 'string' ? route.query.site : '')
const language = ref<'python' | 'sql'>(route.query.language === 'sql' ? 'sql' : 'python')
const isHistoryOpen = ref(false)

const languageOptions = [
  { label: 'Python', value: 'python' },
  { label: 'SQL', value: 'sql' },
]

const siteOptions = computed(() => [
  { label: 'Select site', value: '' },
  ...sites.value.map((site) => ({ label: site.name, value: site.name })),
])

const dbType = computed(
  () => sites.value.find((site) => site.name === selectedSite.value)?.db_type || 'mariadb',
)

watch([selectedSite, language], ([site]) => {
  router.replace({
    path: route.path,
    query: { ...(site ? { site } : {}), language: language.value },
  })
})

onMounted(async () => {
  try {
    sites.value = await databaseApi.sites()
    if (!selectedSite.value && sites.value.length === 1) selectedSite.value = sites.value[0].name
  } catch {}
})
</script>

<template>
  <Teleport defer to="#header-actions">
    <div v-if="selectedSite" class="flex items-center gap-2">
      <Button
        v-if="language === 'python'"
        :variant="isHistoryOpen ? 'subtle' : 'ghost'"
        icon-left="lucide-history"
        @click="isHistoryOpen = !isHistoryOpen"
      >
        History
      </Button>

      <TabButtons v-model="language" :options="languageOptions" />

      <Select
        side="bottom"
        v-model="selectedSite"
        :options="siteOptions"
        class="w-28 sm:w-44 max-w-[140px] sm:max-w-[180px]"
      />
    </div>
  </Teleport>

  <EmptyState
    v-if="!selectedSite"
    class="justify-center h-[calc(100dvh-6.5rem)] md:h-[calc(100dvh-3rem)]"
    icon="lucide-square-terminal"
    title="Select a site to get started"
    description="Python and SQL run against that site."
    :bordered="false"
  >
    <Select side="bottom" v-model="selectedSite" :options="siteOptions" class="w-56" />
  </EmptyState>

  <KeepAlive v-else>
    <PythonConsole
      v-if="language === 'python'"
      v-model:site="selectedSite"
      v-model:history="isHistoryOpen"
    />
    <SQLConsole v-else :site="selectedSite" :db-type="dbType" />
  </KeepAlive>
</template>
