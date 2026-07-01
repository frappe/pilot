<template>
  <div
    class="overflow-auto px-2.5 py-2 max-h-[50vh] font-mono text-sm text-ink-gray-8 bg-surface-gray-3"
    :class="[wrap ? 'whitespace-pre-wrap' : 'whitespace-pre', rounded ? 'rounded-lg' : '']"
  >
    <p v-if="!lines.length" class="text-ink-gray-4">{{ emptyText }}</p>
    <div v-for="(line, index) in lines" :key="index" class="flex gap-3">
      <span v-if="lineNumbers" class="text-ink-gray-4 text-right select-none shrink-0" style="min-width: 1.75rem">
        {{ index + 1 }}
      </span>
      <span class="flex-1" v-html="line || '&nbsp;'" />
    </div>
    <span v-if="streaming" class="inline-block animate-pulse">█</span>
  </div>
</template>

<script setup>
defineProps({
  lines: { type: Array, default: () => [] },
  streaming: { type: Boolean, default: false },
  lineNumbers: { type: Boolean, default: false },
  wrap: { type: Boolean, default: false },
  rounded: { type: Boolean, default: true },
  emptyText: { type: String, default: 'No output.' },
})
</script>
