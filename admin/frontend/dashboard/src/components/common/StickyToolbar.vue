<script setup lang="ts">
import { useShellScrolled } from 'frappe-ui'

const scrolled = useShellScrolled({ threshold: 1 })
</script>

<template>
  <div
    class="top-12 z-10 sticky bg-surface-base py-4"
    :class="scrolled ? 'after:opacity-100' : 'after:opacity-0'"
  >
    <slot />
  </div>
</template>

<style scoped>
div::after {
  content: "";
  background: linear-gradient(to bottom, var(--surface-base), transparent);
  transition: opacity 150ms ease-out;
  @apply top-full absolute inset-x-0 h-8 pointer-events-none;
}
</style>
