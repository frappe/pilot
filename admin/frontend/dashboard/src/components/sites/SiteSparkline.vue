<script setup lang="ts">
import { computed, useId } from 'vue'

const props = defineProps<{ values: number[] }>()

const gradientId = useId()

const weights = [1, 2, 1]

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

const line = computed(() => {
  const max = Math.max(...smoothed.value, 1)
  const step = 100 / Math.max(smoothed.value.length - 1, 1)
  const ys = smoothed.value.map((value) => 36 - (value / max) * 34 - 1)
  const slopes = ys.slice(1).map((y, index) => (y - ys[index]) / step)
  const tangents = ys.map((_, index) => {
    const before = slopes[index - 1] ?? slopes[index] ?? 0
    const after = slopes[index] ?? before

    if (before * after <= 0) return 0
    return (
      Math.sign(before) *
      Math.min(Math.abs(before), Math.abs(after), Math.abs(before + after) / 2) *
      1.5
    )
  })

  return ys
    .map((y, index) => {
      if (!index) return `M0,${y}`

      const x = index * step
      const third = step / 3
      const previous = ys[index - 1]
      return `C${x - step + third},${previous + tangents[index - 1] * third} ${x - third},${y - tangents[index] * third} ${x},${y}`
    })
    .join(' ')
})
</script>

<template>
  <svg viewBox="0 0 100 36" preserveAspectRatio="none" class="text-ink-gray-8">
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
</template>
