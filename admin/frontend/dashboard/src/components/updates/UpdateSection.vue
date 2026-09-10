<script setup lang="ts">
import { Badge } from 'frappe-ui'

interface Props {
  icon: string
  title: string
  // A string too: an unresolved multi-site update counts as "2/5".
  count: number | string
  open?: boolean
}

withDefaults(defineProps<Props>(), {
  open: true,
})

defineEmits(['update:open'])
</script>

<template>
  <details
    :open="open"
    class="group/section rounded-6 border border-outline-gray-2 p-1"
    @toggle="$emit('update:open', $event.target.open)"
  >
    <summary
      class="flex items-center justify-between px-2.5 py-2 rounded-4 transition-colors cursor-pointer select-none hover:bg-surface-gray-1"
    >
      <div class="flex items-center gap-2">
        <span class="size-4 text-ink-gray-5 shrink-0" :class="icon" />
        <h2 class="font-medium text-ink-gray-8">{{ title }}</h2>
        <Badge :label="count" size="sm" />
      </div>

      <span
        class="size-4 text-ink-gray-5 transition-transform group-open/section:rotate-180 lucide-chevron-down"
      />
    </summary>

    <slot />
  </details>
</template>
