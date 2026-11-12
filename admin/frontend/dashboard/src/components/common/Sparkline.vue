<script setup lang="ts">
import { computed, ref, useId } from 'vue'

const props = defineProps<{ values: number[]; unit: string }>()

const gradientId = useId()

const weights = [1, 2, 1]
const hourMs = 3600000
const timeFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', hour: 'numeric' })

const activeIndex = ref(props.values.length - 1)

const smoothed = computed(() =>
  props.values.map((_, index) => {
    let total = 0
    let weight = 0

    weights.forEach((size, offset) => {
      const value = props.values[index + offset - 1]

      if (value === undefined) return
      total += value * size
      weight += size
    })

    return total / weight
  }),
)

const step = computed(() => 100 / Math.max(props.values.length - 1, 1))

const ys = computed(() => {
  const max = Math.max(...smoothed.value, 1)

  return smoothed.value.map((value) => 36 - (value / max) * 34 - 1)
})

const line = computed(() => {
  const slopes = ys.value.slice(1).map((y, index) => (y - ys.value[index]) / step.value)
  const tangents = ys.value.map((_, index) => {
    const before = slopes[index - 1] ?? slopes[index] ?? 0
    const after = slopes[index] ?? before

    if (before * after <= 0) return 0
    return (
      Math.sign(before) *
      Math.min(Math.abs(before), Math.abs(after), Math.abs(before + after) / 2) *
      1.5
    )
  })

  return ys.value
    .map((y, index) => {
      if (!index) return `M0,${y}`

      const x = index * step.value
      const third = step.value / 3
      const previous = ys.value[index - 1]
      return `C${x - step.value + third},${previous + tangents[index - 1] * third} ${x - third},${y - tangents[index] * third} ${x},${y}`
    })
    .join(' ')
})

const activeX = computed(() => activeIndex.value * step.value)
const activeY = computed(() => (ys.value[activeIndex.value] / 36) * 100)
const activeCount = computed(() => props.values[activeIndex.value] ?? 0)

const activeTime = computed(() =>
  timeFormat.format(Date.now() - (props.values.length - 1 - activeIndex.value) * hourMs),
)

const track = (event: PointerEvent) => {
  const { left, width } = (event.currentTarget as HTMLElement).getBoundingClientRect()

  activeIndex.value = Math.round(((event.clientX - left) / width) * (props.values.length - 1))
}
</script>

<template>
  <div class="group relative text-ink-gray-8" @pointermove="track">
    <svg viewBox="0 0 100 36" preserveAspectRatio="none" class="block size-full">
      <defs>
        <linearGradient :id="gradientId" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="currentColor" stop-opacity="0.18" />
          <stop offset="1" stop-color="currentColor" stop-opacity="0" />
        </linearGradient>
      </defs>

      <path :d="`${line} L100,36 L0,36 Z`" :fill="`url(#${gradientId})`" />

      <path
        :d="line"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linejoin="round"
        vector-effect="non-scaling-stroke"
      />
    </svg>

    <!-- hover tooltip -->
    <span
      class="absolute bg-current opacity-0 group-hover:opacity-100 rounded-full ring-4 ring-outline-alpha-gray-3 size-1 transition-all -translate-x-1/2 -translate-y-1/2 duration-100 motion-reduce:transition-none pointer-events-none"
      :style="{ left: `${activeX}%`, top: `${activeY}%` }"
    />

    <p
      class="z-10 absolute bg-surface-gray-10 opacity-0 group-hover:opacity-100 shadow-xl px-2 py-1 rounded-4 text-ink-base text-xs tabular-nums whitespace-nowrap transition-all duration-100 motion-reduce:transition-none pointer-events-none"
      :style="{
        left: `${activeX}%`,
        top: `${activeY}%`,
        transform: `translate(-${activeX}%, calc(-100% - 12px))`,
      }"
    >
      <span class="opacity-70">{{ activeTime }} ·</span>
      {{ activeCount.toLocaleString() }} {{ unit }}{{ activeCount === 1 ? '' : 's' }}
    </p>
  </div>
</template>
