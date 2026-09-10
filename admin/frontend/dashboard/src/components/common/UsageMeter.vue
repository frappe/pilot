<script setup lang="ts">
import { computed, ref } from 'vue'

import { formatBytes } from '@/utils/format'

interface UsagePart {
  label: string
  bytes: number | null
  color: string
  inBar?: boolean // false keeps the part in the legend only
}

interface Props {
  parts: UsagePart[]
  total?: number | null
  legend?: boolean
  barHeight?: string
  visibleCount?: number | null
}

const props = withDefaults(defineProps<Props>(), {
  total: null,
  legend: true,
  barHeight: 'h-7',
  visibleCount: null,
})

const showAll = ref(false)

const formattedParts = computed(() =>
  props.parts.map((part) => ({
    ...part,
    color: `var(--ink-${part.color})`,
    text: part.bytes == null ? '—' : formatBytes(part.bytes),
  })),
)

const visibleParts = computed(() =>
  showAll.value || props.visibleCount == null
    ? formattedParts.value
    : formattedParts.value.slice(0, props.visibleCount),
)

const hiddenCount = computed(() =>
  props.visibleCount == null ? 0 : Math.max(formattedParts.value.length - props.visibleCount, 0),
)

const barParts = computed(() => {
  const known = formattedParts.value.filter(
    (part) => part.inBar !== false && (part.bytes ?? 0) > 0,
  )
  const denominator = props.total ?? known.reduce((sum, part) => sum + (part.bytes ?? 0), 0)
  if (!denominator) return []
  return known.map((part) => ({ ...part, percent: ((part.bytes ?? 0) / denominator) * 100 }))
})
</script>

<template>
  <div class="flex bg-surface-gray-4 rounded-full w-full overflow-hidden" :class="barHeight">
    <div
      v-for="part in barParts"
      :key="part.label"
      :style="{ width: `${part.percent}%`, backgroundColor: part.color }"
      :title="`${part.label}: ${part.text}`"
    />
  </div>

  <dl v-if="legend" class="mt-3">
    <template v-for="part in visibleParts" :key="part.label">
      <slot name="row" :part="part">
        <div class="flex justify-between items-center gap-4 py-2">
          <dt class="flex items-center gap-2 min-w-0">
            <span class="rounded-full size-2 shrink-0" :style="{ backgroundColor: part.color }" />
            <span class="text-ink-gray-7 text-sm truncate">{{ part.label }}</span>
          </dt>

          <dd class="text-ink-gray-8 text-sm tabular-nums shrink-0">{{ part.text }}</dd>
        </div>
      </slot>
    </template>
  </dl>

  <button
    v-if="legend && hiddenCount > 0"
    type="button"
    @click="showAll = !showAll"
    class="flex items-center gap-2 text-sm text-ink-gray-6 hover:text-ink-gray-8 mt-2"
  >
    <span class="size-3.5 lucide-chevron-up" :class="{ 'rotate-180': !showAll }" />
    <span>{{ showAll ? 'Show less' : `Show ${hiddenCount} more` }}</span>
  </button>
</template>
