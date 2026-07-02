<template>
  <slot
    :lines="lines"
    :raw-lines="rawLines"
    :streaming="streaming"
    :set-terminal="setTerminal"
    :scroll-to-bottom="stream.scrollToBottom"
  >
    <TerminalOutput
      ref="terminal"
      :lines="lines"
      :streaming="streaming"
      :line-numbers="lineNumbers"
      :empty-text="emptyText"
      :max-height="maxHeight"
    />
  </slot>
</template>

<script setup>
import { onMounted, watch } from 'vue'
import TerminalOutput from './TerminalOutput.vue'
import { useTaskStream } from '../composables/useTaskStream.js'
import { processLine } from '../utils/ansi.js'

const props = defineProps({
  url: { type: String, default: '' },
  autoStart: { type: Boolean, default: true },
  reset: { type: Boolean, default: true },
  initialLines: { type: Array, default: () => [] },
  guardHiddenTab: { type: Boolean, default: false },
  lineNumbers: { type: Boolean, default: false },
  emptyText: { type: String, default: 'No output yet…' },
  maxHeight: { type: String, default: '65vh' },
})
const emit = defineEmits(['line', 'done', 'error'])

const stream = useTaskStream({ guardHiddenTab: props.guardHiddenTab })
const { terminal, lines, rawLines, streaming } = stream

function setTerminal(el) {
  terminal.value = el
}

function seed(initial) {
  rawLines.value = [...initial]
  lines.value = initial.map(processLine)
}

function start(url = props.url) {
  if (!url) return
  if (props.reset) {
    rawLines.value = []
    lines.value = []
  }
  stream.start(url, {
    onLine: (raw) => emit('line', raw),
    onDone: (success) => emit('done', success),
    onError: () => emit('error'),
  })
}

onMounted(() => {
  if (props.initialLines.length) seed(props.initialLines)
  if (props.autoStart && props.url) start()
})

watch(
  () => props.url,
  (url, previous) => {
    if (props.autoStart && url && url !== previous) start(url)
  },
)

defineExpose({ start, stop: stream.stop, scrollToBottom: stream.scrollToBottom, seed, lines, rawLines, streaming })
</script>
