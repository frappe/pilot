<template>
  <div class="flex flex-col bg-surface-white pt-3 pb-1 border rounded-lg border-outline-gray-2 overflow-hidden">
    <div class="flex justify-between items-baseline gap-2 px-4 pb-3 border-b border-outline-gray-2">
      <h3 class="font-medium text-ink-gray-8 text-base">{{ title }}</h3>
    </div>
    <slot />
  </div>
</template>

<script setup>
defineProps({ title: String, subtitle: String })
</script>
