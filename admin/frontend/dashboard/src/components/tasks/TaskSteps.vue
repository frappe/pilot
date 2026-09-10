<script setup lang="ts">
import { computed, toRef } from 'vue'

import LogView from '@/components/logs/LogView.vue'
import TaskStep from '@/components/tasks/TaskStep.vue'

import { STEP_MARKER_RE, useTaskSteps } from '@/composables/tasks/useTaskSteps'
import { processLine } from '@/utils/ansi'

interface Props {
  rawLines?: any[]
  streaming?: boolean
  taskStatus?: string
  emptyText?: string
}

const props = withDefaults(defineProps<Props>(), {
  rawLines: () => [],
  streaming: false,
  taskStatus: '',
  emptyText: 'No output.',
})

const rawLinesRef = toRef(props, 'rawLines')
const streamingRef = toRef(props, 'streaming')
const taskRef = computed(() => ({ status: props.taskStatus }))

const { stepSections, hasSteps, stepDuration } = useTaskSteps(rawLinesRef, streamingRef, taskRef)
const processedLines = computed(() => props.rawLines.map(processLine))

const sectionLines = (section) => {
  return props.rawLines
    .slice(section.lineStart, section.lineEnd)
    .filter((line) => !STEP_MARKER_RE.test(line))
    .map(processLine)
}

const sectionHasOutput = (section) => {
  return props.rawLines
    .slice(section.lineStart, section.lineEnd)
    .some((line) => line.trim() && !STEP_MARKER_RE.test(line))
}
</script>

<template>
  <div
    v-if="hasSteps"
    class="flex flex-col gap-1 p-1 border border-outline-gray-2 rounded-6 min-w-0"
  >
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
