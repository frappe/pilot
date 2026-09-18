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

const TITLE_WIDTHS = ['w-24', 'w-32', 'w-20', 'w-28']
const DESCRIPTION_WIDTHS = ['w-48', 'w-36', 'w-52', 'w-44']

const titleWidth = computed(() => TITLE_WIDTHS[props.index % TITLE_WIDTHS.length])
const descriptionWidth = computed(
  () => DESCRIPTION_WIDTHS[props.index % DESCRIPTION_WIDTHS.length],
)
</script>

<template>
  <div class="flex items-center gap-3">
    <Skeleton class="rounded-[6px] size-8 shrink-0" />

    <div class="flex-1 py-2 min-w-0">
      <div class="flex items-center h-4">
        <Skeleton class="rounded-4 h-3" :class="titleWidth" />
      </div>

      <div class="flex items-center mt-0.5 h-5">
        <Skeleton class="rounded-4 h-2.5" :class="descriptionWidth" />
      </div>
    </div>
  </div>
</template>
