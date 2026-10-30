<script setup lang="ts">
defineProps<{ percent: number }>()
</script>

<template>
  <div class="relative place-items-center grid size-24 shrink-0">
    <svg
      viewBox="0 0 36 36"
      class="absolute inset-0 -rotate-90"
      fill="none"
      stroke-width="2.5"
      aria-hidden="true"
    >
      <circle cx="18" cy="18" r="15.9155" class="stroke-ink-gray-3" />

      <circle
        cx="18"
        cy="18"
        r="15.9155"
        :stroke-dasharray="`${percent} 100`"
        stroke-linecap="round"
        class="stroke-ink-gray-8 transition-[stroke-dasharray] duration-300"
      />
    </svg>

    <span class="flex flex-col items-center gap-0.5 tabular-nums">
      <span class="text-lg-semibold text-ink-gray-9">{{ percent }}%</span>
      <span class="text-ink-gray-5 text-xs">memory</span>
    </span>
  </div>
</template>
