<script setup lang="ts">
import { Skeleton } from 'frappe-ui'
import { computed } from 'vue'

const props = withDefaults(defineProps<{ index?: number }>(), { index: 0 })

const nameWidths = ['w-32', 'w-24', 'w-40', 'w-28']
const nameWidth = computed(() => nameWidths[props.index % nameWidths.length])
</script>

<template>
  <div
    class="flex flex-col gap-3 bg-surface-gray-0 px-4 pt-3.5 pb-4 border border-outline-gray-1.5 rounded-6 fade-in"
  >
    <div class="flex items-center gap-2 h-4.5">
      <Skeleton class="rounded-full size-2" />
      <Skeleton class="rounded-4 h-3.5" :class="nameWidth" />
    </div>

    <div class="flex items-center gap-3 h-4.5">
      <Skeleton class="rounded-4 w-16 h-3" />
      <Skeleton class="rounded-4 w-12 h-3" />
    </div>

    <Skeleton class="mt-1 rounded-4 h-8" />

    <div class="flex justify-between items-center h-4">
      <Skeleton class="rounded-4 w-24 h-2.5" />
      <Skeleton class="rounded-4 w-20 h-2.5" />
    </div>
  </div>
</template>
