<template>
  <FrappeUIProvider>
    <ReconnectOverlay :paused="awaitingTerminal" />
    <RouterView v-if="isFullScreen" />
    <MainLayout v-else>
      <RouterView />
    </MainLayout>
  </FrappeUIProvider>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useTheme, FrappeUIProvider } from 'frappe-ui'
import ReconnectOverlay from './components/ReconnectOverlay.vue'
import MainLayout from './layouts/MainLayout.vue'
import { useSetupHandoff } from './composables/useSetupHandoff'
import { initializeI18n, useI18n } from './i18n'

const route = useRoute()
const isFullScreen = computed(() => route.meta.fullScreen === true)
const { awaitingTerminal } = useSetupHandoff()
const { initializeTheme } = useTheme()
const { currentLanguage, t } = useI18n()

initializeTheme()
initializeI18n()

watch(
  [() => route.meta?.labelKey, currentLanguage],
  () => {
    if (route.name === 'SiteDetail') return
    const title = route.meta?.labelKey ? t(route.meta.labelKey, route.meta.title) : route.meta?.title
    document.title = title ? `${title} - Pilot` : 'Pilot'
  },
  { immediate: true },
)
</script>
