<script setup lang="ts">
import { nextTick, ref } from 'vue'

import { isSelectionInside } from '@/utils/textSelection'

interface Props {
  lines?: string[]
  streaming?: boolean
  lineNumbers?: boolean
  emptyText?: string
  maxHeight?: string
  fill?: boolean
}

withDefaults(defineProps<Props>(), {
  lines: () => [],
  streaming: false,
  lineNumbers: false,
  emptyText: 'No output.',
  maxHeight: '65vh',
  fill: false,
})

const el = ref<HTMLElement | null>(null)

const scrollToBottom = () => {
  nextTick(() => {
    if (!el.value || isSelectionInside(window.getSelection(), el.value)) return
    el.value.scrollTop = el.value.scrollHeight
  })
}

defineExpose({ scrollToBottom })
</script>

<template>
  <div
    ref="el"
    class="terminal select-text bg-surface-gray-1 text-ink-gray-8 font-mono text-sm leading-[1.6]"
    :class="{ 'terminal--fill': fill }"
    :style="fill ? '' : `max-height:${maxHeight}`"
  >
    <div v-if="!lines.length" class="terminal__empty text-ink-gray-5">{{ emptyText }}</div>
    <div v-for="(line, index) in lines" :key="index" class="terminal__row hover:bg-surface-gray-2">
      <span v-if="lineNumbers" class="terminal__lineno text-ink-gray-4">{{ index + 1 }}</span>
      <span
        class="terminal__text"
        :class="{ 'terminal__text--pad': !lineNumbers }"
        v-html="line || '&nbsp;'"
      />
    </div>

    <div v-if="streaming" class="terminal__cursor text-ink-green-6">█</div>
  </div>
</template>

<style scoped>
.terminal {
  overflow: auto;
  border-radius: 0.5rem;
  min-height: 120px;
  padding: 0.75rem 0;
}
.terminal--fill {
  flex: 1;
  height: 0;
}
.terminal__empty {
  padding: 0.25rem 1rem;
}
.terminal__row {
  display: flex;
}
.terminal__lineno {
  flex-shrink: 0;
  min-width: 3.5rem;
  padding-left: 0.75rem;
  padding-right: 1rem;
  text-align: right;
  user-select: none;
}
.terminal__text {
  flex: 1;
  padding-right: 1rem;
  white-space: pre-wrap;
  word-break: break-all;
}
.terminal__text--pad {
  padding-left: 0.75rem;
}
.terminal__cursor {
  padding: 0.25rem 1rem;
  animation: terminal-blink 1s step-start infinite;
}
@keyframes terminal-blink {
  50% {
    opacity: 0;
  }
}
</style>
