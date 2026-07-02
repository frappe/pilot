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
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useTheme, FrappeUIProvider } from 'frappe-ui'
import ReconnectOverlay from './components/ReconnectOverlay.vue'
import MainLayout from './layouts/MainLayout.vue'
import { useSetupHandoff } from './composables/useSetupHandoff'

const route = useRoute()
const isFullScreen = computed(() => route.meta.fullScreen === true)
const { awaitingTerminal } = useSetupHandoff()
const { initializeTheme } = useTheme()

initializeTheme()
</script>
