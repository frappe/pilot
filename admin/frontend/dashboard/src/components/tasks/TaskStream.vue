<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'

import TerminalOutput from '@/components/common/TerminalOutput.vue'

import { STEP_MARKER_RE } from '@/composables/tasks/useTaskSteps'
import { useTaskStream } from '@/composables/tasks/useTaskStream'
import { processLine } from '@/utils/ansi'

interface Props {
  url?: string
  autoStart?: boolean
  reset?: boolean
  initialLines?: string[]
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

// STEP markers drive the step view; the plain terminal shows only real output.
const visibleLines = computed(() =>
  lines.value.filter((_, index) => !STEP_MARKER_RE.test(rawLines.value[index] ?? '')),
)

const setTerminal = (el: { scrollToBottom: () => void } | null) => {
  terminal.value = el
}

const seed = (initial: string[]) => {
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
      :lines="visibleLines"
      :streaming="streaming"
      :line-numbers="lineNumbers"
      :empty-text="emptyText"
      :max-height="maxHeight"
    />
  </slot>
</template>
