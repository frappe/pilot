<script setup lang="ts">
import { onMounted, watch } from 'vue'

import TerminalOutput from '@/components/common/TerminalOutput.vue'

import { useTaskStream } from '@/composables/tasks/useTaskStream'
import { processLine } from '@/utils/ansi'

interface Props {
  url?: string
  autoStart?: boolean
  reset?: boolean
  initialLines?: any[]
  guardHiddenTab?: boolean
  lineNumbers?: boolean
  emptyText?: string
  maxHeight?: string
}

const props = withDefaults(defineProps<Props>(), {
  url: '',
  autoStart: true,
  reset: true,
  initialLines: () => [],
  guardHiddenTab: false,
  lineNumbers: false,
  emptyText: 'No output yet…',
  maxHeight: '65vh',
})
const emit = defineEmits(['line', 'status', 'done', 'error'])

const stream = useTaskStream({ guardHiddenTab: props.guardHiddenTab })
const { terminal, lines, rawLines, streaming } = stream

const setTerminal = (el) => {
  terminal.value = el
}

const seed = (initial) => {
  rawLines.value = [...initial]
  lines.value = initial.map(processLine)
}

const start = (url = props.url) => {
  if (!url) return
  if (props.reset) {
    rawLines.value = []
    lines.value = []
  }
  stream.start(url, {
    onLine: (raw) => emit('line', raw),
    onStatus: (event) => emit('status', event),
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

defineExpose({
  start,
  stop: stream.stop,
  scrollToBottom: stream.scrollToBottom,
  seed,
  lines,
  rawLines,
  streaming,
})
</script>

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
