<template>
  <FrappeUIProvider>
    <ReconnectOverlay :paused="awaitingTerminal" />
    <LanguageSwitcher v-if="isFullScreen" class="top-4 right-4 z-20 fixed" />
    <RouterView v-if="isFullScreen" />
    <MainLayout v-else>
      <RouterView />
    </MainLayout>
  </FrappeUIProvider>
</template>

<script setup>
import { computed, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { useTheme, FrappeUIProvider } from 'frappe-ui'
import ReconnectOverlay from './components/ReconnectOverlay.vue'
import MainLayout from './layouts/MainLayout.vue'
import LanguageSwitcher from './components/LanguageSwitcher.vue'
import { useSetupHandoff } from './composables/useSetupHandoff'
import { useI18n } from './i18n'

const route = useRoute()
const isFullScreen = computed(() => route.meta.fullScreen === true)
const { awaitingTerminal } = useSetupHandoff()
const { initializeTheme } = useTheme()
const { locale, t } = useI18n()

watchEffect(() => {
  locale.value
  if (route.name !== 'SiteDetail') {
    document.title = route.meta?.titleKey ? `${t(route.meta.titleKey)} - Pilot` : 'Pilot'
  }
})

initializeTheme()
</script>
