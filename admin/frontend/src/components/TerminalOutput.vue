<script setup>
import { ref, nextTick } from 'vue'

const props = defineProps({
  lines: { type: Array, default: () => [] },
  streaming: { type: Boolean, default: false },
  lineNumbers: { type: Boolean, default: false },
  emptyText: { type: String, default: 'No output.' },
  maxHeight: { type: String, default: '65vh' },
})

const el = ref(null)

function scrollToBottom() {
  nextTick(() => { if (el.value) el.value.scrollTop = el.value.scrollHeight })
}

defineExpose({ scrollToBottom })
</script>

<template>
  <div
    ref="el"
    class="overflow-auto rounded-lg font-mono text-sm leading-[1.6] select-text"
    :style="`background:#1e1e2e; color:#cdd6f4; padding: 0.75rem 0; max-height:${maxHeight}; min-height:120px;`"
  >
    <div v-if="!lines.length" class="px-4 py-1" style="color:#585b70;">{{ emptyText }}</div>
    <template v-else>
      <div
        v-for="(line, i) in lines"
        :key="i"
        class="group flex hover:bg-white/[0.04]"
      >
        <span
          v-if="lineNumbers"
          class="select-none shrink-0 pr-4 text-right"
          style="color:#45475a; min-width:3.5rem; padding-left:0.75rem;"
        >{{ i + 1 }}</span>
        <span
          class="flex-1 break-all pr-4 whitespace-pre-wrap"
          :style="lineNumbers ? '' : 'padding-left:0.75rem'"
          v-html="line || '&nbsp;'"
        />
      </div>
    </template>
    <div v-if="streaming" class="px-4 py-1">
      <span style="color:#a6e3a1;" class="animate-pulse">█</span>
    </div>
  </div>
</template>
