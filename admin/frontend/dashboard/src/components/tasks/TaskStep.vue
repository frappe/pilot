<script setup lang="ts">
import { Spinner } from 'frappe-ui'
import { ref, watch } from 'vue'

import CopyBtn from '@/components/common/CopyBtn.vue'
import LogView from '@/components/logs/LogView.vue'

interface Props {
  label: string
  status?: string
  duration?: string | null
  lines?: string[]
  hasOutput?: boolean
  streaming?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  status: 'pending',
  duration: null,
  lines: () => [],
  hasOutput: false,
  streaming: false,
})

// Open while running or failed; anything else settles closed unless toggled.
const shouldExpand = (status: string) => {
  return status === 'running' || status === 'failed'
}

const expanded = ref(shouldExpand(props.status))
let userOverridden = false

watch(
  () => props.status,
  (status) => {
    if (!userOverridden) expanded.value = shouldExpand(status)
  },
)

const toggle = () => {
  if (!props.hasOutput) return
  userOverridden = true
  expanded.value = !expanded.value
}
</script>

<template>
  <div>
    <button
      type="button"
      class="flex items-center gap-3 px-2.5 py-2 rounded-4 w-full text-left transition-colors"
      :class="hasOutput ? 'cursor-pointer hover:bg-surface-gray-1' : 'cursor-default'"
      :aria-expanded="hasOutput ? expanded : undefined"
      @click="toggle"
    >
      <span v-if="status === 'done'" class="size-5 text-ink-gray-5 shrink-0 lucide-circle-check" />
      <Spinner v-else-if="status === 'running'" size="md" class="text-ink-amber-6 shrink-0" />
      <span
        v-else-if="status === 'failed'"
        class="size-5 text-ink-red-6 shrink-0 lucide-circle-x"
      />
      <span v-else class="size-5 text-ink-gray-3 shrink-0 lucide-circle-dashed" />

      <span
        class="flex-1 min-w-0 truncate"
        :class="status === 'pending' ? 'text-ink-gray-4' : 'font-medium text-ink-gray-9'"
      >
        {{ label }}
      </span>

      <span class="w-16 text-ink-gray-5 text-sm text-right tabular-nums shrink-0">
        <template v-if="duration">{{ duration }}</template>
        <span v-else-if="status === 'running'" class="animate-pulse">running</span>
      </span>

      <span
        class="size-4 text-ink-gray-4 transition-transform shrink-0 lucide-chevron-down"
        :class="[hasOutput ? '' : 'invisible', expanded ? 'rotate-180' : '']"
      />
    </button>

    <div v-if="expanded && hasOutput" class="relative mt-1">
      <LogView :lines="lines" :streaming="streaming" />
      <CopyBtn
        :text="lines.join('\n')"
        class="top-2 right-4 absolute bg-surface-gray-1 p-1 rounded-4 text-ink-gray-5 hover:text-ink-gray-8 transition-colors"
      />
    </div>
  </div>
</template>
