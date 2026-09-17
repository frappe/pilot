<script setup lang="ts">
import { ref, nextTick } from 'vue'

import { isSelectionInside } from '@/utils/textSelection'

interface Props {
  lines?: any[]
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

const el = ref(null)

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
    class="terminal select-text font-mono text-sm leading-[1.6]"
    :class="{ 'terminal--fill': fill }"
    :style="fill ? '' : `max-height:${maxHeight}`"
  >
    <div v-if="!lines.length" class="terminal__empty">{{ emptyText }}</div>
    <div v-for="(line, index) in lines" :key="index" class="terminal__row">
      <span v-if="lineNumbers" class="terminal__lineno">{{ index + 1 }}</span>
      <span
        class="terminal__text"
        :class="{ 'terminal__text--pad': !lineNumbers }"
        v-html="line || '&nbsp;'"
      />
    </div>

    <div v-if="streaming" class="terminal__cursor">█</div>
  </div>
</template>

<style scoped>
.terminal {
  --terminal-bg: #1e1e2e;
  --terminal-fg: #cdd6f4;
  --terminal-muted: #585b70;
  --terminal-lineno: #45475a;
  --terminal-cursor: #a6e3a1;
  overflow: auto;
  border-radius: 0.5rem;
  min-height: 120px;
  padding: 0.75rem 0;
  background: var(--terminal-bg);
  color: var(--terminal-fg);
}
/* The terminal stays dark in both app themes, so the global ::selection is invisible here. */
.terminal ::selection {
  background: rgb(255 255 255 / 0.25);
}
.terminal--fill {
  flex: 1;
  height: 0;
}
.terminal__empty {
  padding: 0.25rem 1rem;
  color: var(--terminal-muted);
}
.terminal__row {
  display: flex;
}
.terminal__row:hover {
  background: rgb(255 255 255 / 0.04);
}
.terminal__lineno {
  flex-shrink: 0;
  min-width: 3.5rem;
  padding-left: 0.75rem;
  padding-right: 1rem;
  text-align: right;
  user-select: none;
  color: var(--terminal-lineno);
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
  color: var(--terminal-cursor);
  animation: terminal-blink 1s step-start infinite;
}
@keyframes terminal-blink {
  50% {
    opacity: 0;
  }
}
</style>
