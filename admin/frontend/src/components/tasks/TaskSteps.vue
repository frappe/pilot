<template>
  <div v-if="hasSteps" class="space-y-2.5">
    <TaskStep
      v-for="section in stepSections"
      :key="section.key"
      :label="section.label"
      :status="section.status"
      :duration="stepDuration(section)"
      :lines="sectionLines(section)"
      :has-output="sectionHasOutput(section)"
      :streaming="streaming && section.status === 'running'"
    />
  </div>
  <LogView v-else :lines="processedLines" :streaming="streaming" :empty-text="emptyText" />
</template>

<script setup>
import { computed, toRef } from 'vue'
import LogView from '../logs/LogView.vue'
import TaskStep from './TaskStep.vue'
import { STEP_MARKER_RE, useTaskSteps } from '@/composables/tasks/useTaskSteps'
import { processLine } from '@/utils/ansi'

const props = defineProps({
  rawLines: { type: Array, default: () => [] },
  streaming: { type: Boolean, default: false },
  taskStatus: { type: String, default: '' },
  emptyText: { type: String, default: 'No output.' },
})

const rawLinesRef = toRef(props, 'rawLines')
const streamingRef = toRef(props, 'streaming')
const taskRef = computed(() => ({ status: props.taskStatus }))

const { stepSections, hasSteps, stepDuration } = useTaskSteps(rawLinesRef, streamingRef, taskRef)
const processedLines = computed(() => props.rawLines.map(processLine))

function sectionLines(section) {
  return props.rawLines
    .slice(section.lineStart, section.lineEnd)
    .filter((line) => !STEP_MARKER_RE.test(line))
    .map(processLine)
}

function sectionHasOutput(section) {
  return props.rawLines
    .slice(section.lineStart, section.lineEnd)
    .some((line) => line.trim() && !STEP_MARKER_RE.test(line))
}
</script>
