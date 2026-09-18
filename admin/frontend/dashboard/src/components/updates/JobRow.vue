<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  job: Record<string, any>
}

const props = defineProps<Props>()

// A retried step leaves several jobs behind, so only the one that broke the chain
// earns colour. Killing a migrate mid-run stops the update just as a crash does.
const failed = computed(() => ['failed', 'killed'].includes(props.job.status))
</script>

<template>
  <button
    type="button"
    class="flex items-center gap-1.5 px-2.5 py-2 rounded-4 transition-colors w-full hover:bg-surface-gray-1 text-sm text-left"
    :class="failed ? 'text-ink-red-6' : 'text-ink-gray-7'"
  >
    <span
      class="size-4 shrink-0"
      :class="failed ? 'lucide-circle-x' : 'lucide-square-terminal'"
    />
    {{ job.label }}
  </button>
</template>
