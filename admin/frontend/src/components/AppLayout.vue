<script setup>
import { computed, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { Breadcrumbs, Button } from 'frappe-ui'
import LucideMenu from '~icons/lucide/menu'
import AppSidebar from './AppSidebar.vue'
import SettingsModal from './SettingsModal.vue'
import BenchSwitcherDialog from './BenchSwitcherDialog.vue'
import NewBenchDialog from './NewBenchDialog.vue'

const emit = defineEmits(['logout'])

const route = useRoute()
const showSettings = ref(false)
const showChangeBench = ref(false)
const showNewBench = ref(false)
const sidebarOpen = ref(false)

// On mobile the sidebar is an overlay drawer; close it whenever navigation happens.
watch(() => route.fullPath, () => { sidebarOpen.value = false })

const breadcrumbs = computed(() => {
  const { path, params } = route

  if (path === '/' || path === '/sites') return [{ label: 'Sites' }]
  if (path.startsWith('/sites/')) return [
    { label: 'Sites', route: '/' },
    { label: String(params.name) },
  ]
if (path === '/marketplace') return [{ label: 'Marketplace' }]
  if (path === '/monitor') return [{ label: 'Monitor' }]
  if (path === '/logs') return [{ label: 'Logs' }]
  if (path === '/tasks') return [{ label: 'Tasks' }]
  if (path.startsWith('/tasks/')) return [{ label: 'Tasks', route: '/tasks' }, { label: String(params.id) }]
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
    <!-- Backdrop behind the mobile drawer -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-20 bg-black/30 md:hidden"
      @click="sidebarOpen = false"
    />
    <!-- Off-canvas drawer below md, static sidebar from md up -->
    <div
      class="fixed inset-y-0 left-0 z-30 transition-transform duration-300 ease-in-out md:static md:z-auto md:translate-x-0"
      :class="sidebarOpen ? 'translate-x-0' : '-translate-x-full'"
    >
      <AppSidebar
        @logout="$emit('logout')"
        @open-settings="showSettings = true"
        @change-bench="showChangeBench = true"
      />
    </div>
    <main class="flex-1 overflow-hidden flex flex-col bg-surface-white">
      <header class="shrink-0 sticky top-0 z-[10] flex items-center gap-2 border-b bg-surface-white px-5 py-2.5">
        <Button variant="ghost" class="md:hidden" @click="sidebarOpen = true">
          <template #icon><LucideMenu class="h-4 w-4" /></template>
        </Button>
        <Breadcrumbs :items="breadcrumbs" />
        <div id="header-actions" class="ml-auto flex items-center gap-2" />
      </header>
      <div class="p-6 flex-1 overflow-auto min-h-0">
        <RouterView />
      </div>
    </main>
    <SettingsModal v-model="showSettings" />
    <BenchSwitcherDialog v-model="showChangeBench" @new-bench="showNewBench = true" />
    <NewBenchDialog v-model="showNewBench" />
  </div>
</template>
