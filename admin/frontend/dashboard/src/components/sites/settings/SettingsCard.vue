<script setup lang="ts">
defineProps<{ title: string }>()
</script>

<template>
  <section class="border border-outline-gray-1.5 rounded-6 overflow-hidden">
    <h2
      class="flex items-center gap-2 bg-surface-gray-0 px-4 border-outline-gray-1.5 border-b h-10 text-base-medium text-ink-gray-8"
    >
      {{ title }}
    </h2>

    <div class="px-4 divide-y divide-outline-gray-1">
      <slot />
    </div>
  </section>
</template>
