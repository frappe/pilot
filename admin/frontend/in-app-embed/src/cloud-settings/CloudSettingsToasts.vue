<script setup lang="ts">
import { ToastProvider } from 'frappe-ui'
import TailwindStyles from './TailwindStyles.vue'
import { usePageDark } from './theme'

const isDark = usePageDark()
</script>

<template>
  <div class="cloud-settings-root" :data-theme="isDark ? 'dark' : 'light'">
    <ToastProvider />
    <TailwindStyles />
  </div>
</template>
