<script setup lang="ts">
defineProps<{ icon: string; text: string }>()
</script>

<template>
  <div
    class="flex flex-col flex-1 justify-center items-center gap-2.5 min-h-28 text-ink-gray-5 text-sm"
  >
    <span
      class="place-items-center grid border border-outline-gray-3 border-dashed rounded-4 size-9"
    >
      <span class="size-4 text-ink-gray-5" :class="icon" />
    </span>
    {{ text }}
  </div>
</template>
