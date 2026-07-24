<template>
  <div class="p-4">
    <div class="flex bg-surface-gray-2 rounded-md w-full h-7 overflow-hidden">
      <div
        v-for="part in barParts"
        :key="part.label"
        :style="{ width: `${part.percent}%`, backgroundColor: part.color }"
        :title="`${part.label}: ${part.text}`"
      />
    </div>

    <dl class="mt-3">
      <div
        v-for="part in parts"
        :key="part.label"
        class="flex justify-between items-center gap-4 py-2.5 border-b border-outline-gray-1 last:border-b-0"
      >
        <dt class="flex items-center gap-2 min-w-0">
          <span class="rounded-full size-2 shrink-0" :style="{ backgroundColor: part.color }" />
          <span class="text-ink-gray-7 text-sm truncate">{{ part.label }}</span>
        </dt>
        <dd class="text-ink-gray-8 text-sm tabular-nums">{{ part.text }}</dd>
      </div>
    </dl>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { formatBytes } from '@/utils/format'

const props = defineProps({
  size: { type: Object, required: true },
})

// Matches the palette used by the dashboard charts.
const COLORS = {
  data: '#ef4444',
  index: '#06b6d4',
  claimable: '#f59e0b',
  free: '#d1d5db',
}

const parts = computed(() =>
  [
    { label: 'Data Size', bytes: props.size.data_bytes, color: COLORS.data },
    { label: 'Index Size', bytes: props.size.index_bytes, color: COLORS.index },
    { label: 'Claimable Space', bytes: props.size.claimable_bytes, color: COLORS.claimable },
    { label: 'Free Space', bytes: props.size.free_bytes, color: COLORS.free },
  ].map((part) => ({ ...part, text: part.bytes == null ? '—' : formatBytes(part.bytes) })),
)

// A metric the engine could not report has no share of the bar.
const barParts = computed(() => {
  const known = parts.value.filter((part) => part.bytes > 0)
  const total = known.reduce((sum, part) => sum + part.bytes, 0)
  if (!total) return []
  return known.map((part) => ({ ...part, percent: (part.bytes / total) * 100 }))
})
</script>
