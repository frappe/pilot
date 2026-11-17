<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Alert } from 'frappe-ui'

import CopyBtn from '@/components/common/CopyBtn.vue'
import LogView from '@/components/logs/LogView.vue'

interface Props {
  icon?: string
  label: string
  status?: string
  duration?: string | null
  lines?: string[]
  hasOutput?: boolean
  streaming?: boolean
  failure?: string
}

const props = withDefaults(defineProps<Props>(), {
  icon: '',
  status: 'pending',
  duration: null,
  lines: () => [],
  hasOutput: false,
  streaming: false,
  failure: '',
})

const iconTone: Record<string, string> = {
  done: 'text-ink-gray-5',
  running: 'text-ink-gray-8 animate-pulse',
  failed: 'text-ink-red-6',
}

const note = computed(() => {
  const time = props.duration && props.duration !== '0.0s' ? props.duration : ''

  if (props.status === 'running') return time || 'Running'
  if (props.status === 'failed') return time || 'Failed'

  return time || 'Done'
})

const canOpen = computed(() => props.hasOutput || Boolean(props.failure))

const shouldExpand = (status: string) => status === 'running' || status === 'failed'

const expanded = ref(shouldExpand(props.status))
let userOverridden = false

watch(
  () => props.status,
  (status) => {
    if (!userOverridden) expanded.value = shouldExpand(status)
  },
)

const toggle = () => {
  if (!canOpen.value) return

  userOverridden = true
  expanded.value = !expanded.value
}
</script>

<template>
  <li
    class="relative grid grid-cols-[16px_minmax(0,1fr)] gap-x-3 pb-5 last:pb-0 step-thread step-thread-compact"
    :class="status !== 'done' && 'step-thread-open'"
  >
    <span
      class="mt-1 size-4"
      :class="[icon || 'lucide-terminal', iconTone[status] || iconTone.done]"
    />

    <div class="min-w-0">
      <button
        type="button"
        class="group flex items-center gap-3 w-full min-h-6 text-left"
        :class="canOpen ? 'cursor-pointer' : 'cursor-default'"
        :aria-expanded="canOpen ? expanded : undefined"
        @click="toggle"
      >
        <span
          class="flex-1 min-w-0 text-p-base sm:text-base text-ink-gray-8 sm:truncate"
          :class="canOpen && 'group-hover:text-ink-gray-9'"
        >
          {{ label }}
        </span>

        <span
          class="flex justify-end items-center gap-1 sm:w-24 text-sm tabular-nums shrink-0"
          :class="status === 'failed' ? 'text-ink-red-6' : 'text-ink-gray-5'"
        >
          <span v-if="status === 'done'" class="size-3.5 text-ink-green-6 lucide-check" />
          <span v-else-if="status === 'failed'" class="size-3.5 lucide-x" />
          {{ note }}
        </span>

        <span
          class="size-4 text-ink-gray-4 transition-transform shrink-0 lucide-chevron-down"
          :class="[canOpen ? '' : 'invisible', expanded ? 'rotate-180' : '']"
        />
      </button>

      <Alert v-if="expanded && failure" class="mt-3" theme="red" :title="failure" />

      <div v-if="expanded && hasOutput" class="relative mt-3">
        <LogView :lines="lines" :streaming="streaming" />
        <CopyBtn
          :text="lines.join('\n')"
          class="top-2 right-4 absolute bg-surface-gray-1 p-1 rounded-4 text-ink-gray-5 hover:text-ink-gray-8 transition-colors"
        />
      </div>
    </div>
  </li>
</template>
