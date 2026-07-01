<template>
  <div class="border rounded-lg min-w-0 border-outline-gray-2 overflow-hidden">
    <div class="flex items-center gap-3 bg-surface-white px-4 py-2.5 transition-colors"
      :class="hasOutput ? 'cursor-pointer hover:bg-surface-gray-1' : ''" @click="toggle">
      <span class="place-items-center grid rounded-full size-6 shrink-0" :class="iconBg">
        <span v-if="status === 'done'" class="size-3.5 lucide-check" />
        <span v-else-if="status === 'running'" class="size-3.5 animate-spin lucide-loader-circle" />
        <span v-else-if="status === 'failed'" class="size-3.5 lucide-x" />
        <span v-else class="bg-ink-gray-3 rounded-full size-1.5" />
      </span>
      <span class="flex-1 min-w-0 text-sm truncate" :class="status === 'pending' ? 'text-ink-gray-4' : 'font-medium text-ink-gray-9'">
        {{ label }}
      </span>
      <span class="text-ink-gray-5 text-xs shrink-0">
        <template v-if="duration">{{ duration }}</template>
        <span v-else-if="status === 'running'" class="animate-pulse">running…</span>
      </span>
      <span v-if="hasOutput" class="size-4 text-ink-gray-4 transition-transform shrink-0 lucide-chevron-down"
        :class="{ 'rotate-180': expanded }" />
    </div>
    <LogView v-if="expanded && hasOutput" :lines="lines" :streaming="streaming" :rounded="false" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import LogView from './LogView.vue'

const props = defineProps({
  label: { type: String, required: true },
  status: { type: String, default: 'pending' },
  duration: { type: String, default: null },
  lines: { type: Array, default: () => [] },
  hasOutput: { type: Boolean, default: false },
  streaming: { type: Boolean, default: false },
})

// Auto-expanded while running, auto-collapsed once it settles — unless the
// user has manually toggled it, in which case their choice sticks.
const expanded = ref(props.status === 'running')
let userOverridden = false

watch(
  () => props.status,
  (status) => {
    if (!userOverridden) expanded.value = status === 'running'
  },
)

function toggle() {
  if (!props.hasOutput) return
  userOverridden = true
  expanded.value = !expanded.value
}

const STATUS_ICON_BG = {
  done: 'bg-surface-green-2 text-ink-green-8',
  running: 'bg-surface-amber-2 text-ink-amber-8',
  failed: 'bg-surface-red-2 text-ink-red-8',
  pending: 'bg-ink-gray-1',
}

const iconBg = computed(() => STATUS_ICON_BG[props.status] || STATUS_ICON_BG.pending)
</script>
