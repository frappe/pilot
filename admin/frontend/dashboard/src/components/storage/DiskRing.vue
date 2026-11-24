<script setup lang="ts">
import { computed } from 'vue'

interface Segment {
  key: string
  bytes: number
  color: string
}

interface Props {
  segments: Segment[]
  total: number
  size?: number
  thickness?: number
  active?: string
  start?: number
}

const emit = defineEmits<{ hover: [key: string] }>()

const props = withDefaults(defineProps<Props>(), { size: 176, thickness: 13, start: 0, active: '' })

const length = 1000
const gap = 6

const center = computed(() => props.size / 2)
const radius = computed(() => (props.size - props.thickness) / 2 - 4)

const arcs = computed(() => {
  let offset = 0

  return props.segments.map((segment) => {
    const share = props.total ? (segment.bytes / props.total) * length : 0
    const arc = { ...segment, dash: Math.max(share - gap, 0.5), offset }

    offset += share

    return arc
  })
})
</script>

<template>
  <div
    class="relative place-items-center grid shrink-0"
    :style="{ width: `${size}px`, height: `${size}px` }"
  >
    <svg
      :viewBox="`0 0 ${size} ${size}`"
      class="absolute inset-0 overflow-visible -rotate-90"
      aria-hidden="true"
    >
      <circle
        :cx="center"
        :cy="center"
        :r="radius"
        fill="var(--surface-base)"
        stroke="var(--surface-gray-2)"
        :stroke-width="thickness"
      />

      <g :transform="`rotate(${start} ${center} ${center})`">
        <circle
          v-for="arc in arcs"
          :key="arc.key"
          :cx="center"
          :cy="center"
          :r="radius"
          fill="none"
          :pathLength="length"
          :style="{ '--slice-color': arc.color }"
          :class="{ 'ring-slice-active': active === arc.key }"
          class="ring-slice"
          @pointerenter="emit('hover', arc.key)"
          @pointerleave="emit('hover', '')"
          :stroke-width="thickness"
          :stroke-dasharray="`${arc.dash} ${length}`"
          :stroke-dashoffset="-arc.offset"
        />
      </g>
    </svg>

    <span class="relative flex flex-col items-center text-center">
      <slot />
    </span>
  </div>
</template>

<style scoped>
.ring-slice {
  cursor: pointer;
  stroke: var(--slice-color);
  transition: stroke 200ms ease-out;
}

.ring-slice-active {
  stroke: var(--ink-gray-8);
}

@media (prefers-reduced-motion: reduce) {
  .ring-slice {
    transition: none;
  }
}
</style>
