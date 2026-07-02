<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Breadcrumbs } from 'frappe-ui'
import AppSidebar from '@/components/AppSidebar.vue'
import { useBreadcrumbs } from '@/composables/useBreadcrumbs'
import { useIsMobile } from '@/composables/useIsMobile'

const route = useRoute()
const { items, resetBreadcrumbs } = useBreadcrumbs()
const isMobile = useIsMobile()

watch(() => route.name, resetBreadcrumbs)

const breadcrumbs = computed(() => {
  const all = items.value || breadcrumbsFromRouteMeta(route.meta)
  return isMobile.value ? all.slice(-1) : all
})

function breadcrumbsFromRouteMeta({ title = '', group }) {
  return group ? [{ label: group }, { label: title }] : [{ label: title }]
}
</script>

<template>
  <div class="flex bg-surface-elevation-1 h-screen overflow-hidden">
    <AppSidebar />
    <main class="flex flex-col flex-1 overflow-hidden">
      <header
        class="top-0 z-10 sticky flex items-center gap-2 px-4 sm:px-6 py-2.5 border-b border-outline-alpha-gray-1 shrink-0">
        <Breadcrumbs :items="breadcrumbs" />
        <div id="header-actions" class="flex items-center gap-2 ml-auto" />
      </header>
      <div class="flex-1 p-4 sm:p-6 min-h-0 overflow-auto">
        <slot />
      </div>
    </main>
  </div>
</template>
