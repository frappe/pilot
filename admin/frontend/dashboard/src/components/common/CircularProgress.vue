<script setup lang="ts">
withDefaults(defineProps<{ value: number; tone?: string; track?: string }>(), {
  tone: 'text-ink-gray-8',
  track: 'text-ink-gray-3',
})
</script>

<template>
  <svg viewBox="0 0 16 16" fill="none" stroke-width="2" class="-rotate-90" aria-hidden="true">
    <circle cx="8" cy="8" r="6" class="stroke-current" :class="track" />

    <circle
      cx="8"
      cy="8"
      r="6"
      pathLength="100"
      stroke-linecap="round"
      :stroke-dasharray="`${value} 100`"
      class="stroke-current transition-[stroke-dasharray] duration-500"
      :class="tone"
    />
  </svg>
</template>
