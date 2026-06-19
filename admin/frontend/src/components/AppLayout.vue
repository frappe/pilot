<script setup>
import { computed, ref } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { Breadcrumbs } from 'frappe-ui'
import AppSidebar from './AppSidebar.vue'
import SettingsModal from './SettingsModal.vue'
import BenchSwitcherDialog from './BenchSwitcherDialog.vue'
import NewBenchDialog from './NewBenchDialog.vue'

const emit = defineEmits(['logout'])

const route = useRoute()
const showSettings = ref(false)
const showChangeBench = ref(false)
const showNewBench = ref(false)

const breadcrumbs = computed(() => {
  const { path, params } = route

  if (path === '/' || path === '/sites') return [{ label: 'Sites' }]
  if (path.startsWith('/sites/')) return [
    { label: 'Sites', route: '/' },
    { label: String(params.name) },
  ]
  if (path === '/apps') return [{ label: 'Apps' }]
  if (path === '/marketplace') return [{ label: 'Marketplace' }]
  if (path === '/monitor') return [{ label: 'Monitor' }]
  if (path === '/logs') return [{ label: 'Logs' }]
  if (path === '/tasks') return [{ label: 'Tasks' }]
  if (path === '/database') return [{ label: 'Database' }]
  if (path.startsWith('/database/binlogs/')) return [
    { label: 'Database' },
    { label: 'Binary Logs', route: '/database/binlogs' },
    { label: String(params.name) },
  ]
  return [{ label: '' }]
})
</script>

<template>
  <div class="flex h-screen overflow-hidden">
    <AppSidebar
      @logout="$emit('logout')"
      @open-settings="showSettings = true"
      @change-bench="showChangeBench = true"
      @new-bench="showNewBench = true"
    />
    <main class="flex-1 overflow-hidden flex flex-col bg-surface-white">
      <header class="shrink-0 sticky top-0 z-[10] flex items-center border-b bg-surface-white px-5 py-2.5">
        <Breadcrumbs :items="breadcrumbs" />
        <div id="header-actions" class="ml-auto flex items-center gap-2" />
      </header>
      <div class="p-6 flex-1 overflow-auto min-h-0">
        <RouterView />
      </div>
    </main>
    <SettingsModal v-model="showSettings" />
    <BenchSwitcherDialog v-model="showChangeBench" />
    <NewBenchDialog v-model="showNewBench" />
  </div>
</template>
