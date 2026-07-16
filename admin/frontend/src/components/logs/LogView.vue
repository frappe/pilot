<template>
  <div ref="el" class="bg-surface-gray-3 overflow-auto font-mono text-ink-gray-8 text-sm"
    :class="[wrap ? 'whitespace-pre-wrap' : 'whitespace-pre', rounded ? 'rounded-sm sm:rounded-lg' : '', fill ? 'flex-1 h-0' : 'max-h-[50vh]', divided ? '' : 'px-2.5 py-2']">
    <p v-if="!lines.length" class="px-2.5 py-2.5 text-ink-gray-4">{{ emptyText }}</p>
    <div v-for="(line, index) in lines" :key="index" class="flex gap-3"
      :class="divided ? 'border-b border-outline-gray-2 px-2 py-1.5 last:border-0 sm:px-4' : ''">
      <span v-if="lineNumbers" class="text-ink-gray-4 text-right select-none shrink-0" style="min-width: 1.75rem">
        {{ index + 1 }}
      </span>
      <span class="flex-1" :class="wrap ? 'break-all' : ''" v-html="line || '&nbsp;'" />
    </div>
    <span v-if="streaming" class="inline-block animate-pulse" :class="divided ? 'px-3 py-1 sm:px-4' : ''">█</span>
  </div>
</template>

<script setup>
import { nextTick, ref, computed } from 'vue'

const props = defineProps({
  lines: { type: Array, default: () => [] },
  streaming: { type: Boolean, default: false },
  lineNumbers: { type: Boolean, default: false },
  wrap: { type: Boolean, default: false },
  rounded: { type: Boolean, default: true },
  fill: { type: Boolean, default: false },
  divided: { type: Boolean, default: false },
  emptyText: { type: String, default: 'No output.' },
})

const el = ref(null)

function scrollToBottom() {
  nextTick(() => { if (el.value) el.value.scrollTop = el.value.scrollHeight })
}

defineExpose({ scrollToBottom })
</script>
