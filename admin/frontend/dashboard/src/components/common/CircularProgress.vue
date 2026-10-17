<script setup lang="ts">
defineProps<{ value: number }>()
</script>

<template>
  <svg
    viewBox="0 0 16 16"
    fill="none"
    stroke-width="2"
    class="size-4 -rotate-90"
    aria-hidden="true"
  >
    <circle cx="8" cy="8" r="6" class="stroke-current text-ink-gray-3" />

    <circle
      cx="8"
      cy="8"
      r="6"
      pathLength="100"
      stroke-linecap="round"
      :stroke-dasharray="`${value} 100`"
      class="stroke-current text-ink-gray-8 transition-[stroke-dasharray] duration-500"
    />
  </svg>
</template>
