<script setup lang="ts">
import { computed } from 'vue'
import { Skeleton } from 'frappe-ui'

interface Props {
  // Index-based width cycle: varied bars, stable across re-renders.
  index?: number
}

const props = withDefaults(defineProps<Props>(), {
  index: 0,
})

const NAME_WIDTHS = ['w-32', 'w-24', 'w-40', 'w-28']
const nameWidth = computed(() => NAME_WIDTHS[props.index % NAME_WIDTHS.length])
</script>

<template>
  <div
    class="flex items-center gap-3 bg-surface-base p-2 sm:px-3 sm:py-2 border rounded-6 border-outline-gray-2"
  >
    <Skeleton class="rounded-4 size-8 shrink-0" />
    <div class="flex-1 min-w-0">
      <div class="flex items-center h-6">
        <Skeleton class="rounded-4 h-3.5" :class="nameWidth" />
      </div>

      <div class="flex items-center h-5">
        <Skeleton class="rounded-4 w-12 h-2.5" />
      </div>
    </div>
  </div>
</template>
