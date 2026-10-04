<script setup lang="ts">
import { ScrollAreaRoot, ScrollAreaScrollbar, ScrollAreaThumb, ScrollAreaViewport } from 'reka-ui'

interface Props {
  viewportClass?: string
}

defineProps<Props>()
</script>

<template>
  <ScrollAreaRoot type="auto" class="relative overflow-hidden" style="--scrollbar-size: 10px">
    <ScrollAreaViewport :class="['h-full w-full', viewportClass]">
      <slot />
    </ScrollAreaViewport>

    <ScrollAreaScrollbar
      class="z-20 flex w-2.5 touch-none select-none p-0.5 transition-colors duration-[160ms] ease-out"
      orientation="vertical"
    >
      <ScrollAreaThumb class="relative flex-1 rounded-[10px] bg-surface-gray-3" />
    </ScrollAreaScrollbar>
  </ScrollAreaRoot>
</template>
