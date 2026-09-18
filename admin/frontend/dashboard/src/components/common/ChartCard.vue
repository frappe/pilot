<script setup lang="ts">
interface Props {
  title?: string
  subtitle?: string
}

defineProps<Props>()
</script>

<template>
  <div
    class="flex flex-col bg-surface-base pt-3 pb-2 px-4 border rounded-4 border-outline-gray-2 overflow-hidden"
  >
    <h3 class="text-base-medium">{{ title }}</h3>

    <slot />
  </div>
</template>
