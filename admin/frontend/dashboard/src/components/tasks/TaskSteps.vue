<script setup lang="ts">
import { computed, toRef } from 'vue'
import { Alert } from 'frappe-ui'

import LogView from '@/components/logs/LogView.vue'
import TaskStep from '@/components/tasks/TaskStep.vue'

import { STEP_MARKER_RE, type StepSection, useTaskSteps } from '@/composables/tasks/useTaskSteps'
import { processLine } from '@/utils/ansi'

interface Props {
  rawLines?: string[]
  streaming?: boolean
  taskStatus?: string
  finishedAt?: string | null
  emptyText?: string
  failure?: string
}

const props = withDefaults(defineProps<Props>(), {
  rawLines: () => [],
  streaming: false,
  taskStatus: '',
  finishedAt: null,
  emptyText: 'No output.',
  failure: '',
})

const rawLinesRef = toRef(props, 'rawLines')
const streamingRef = toRef(props, 'streaming')
const taskRef = computed(() => ({ status: props.taskStatus, finished_at: props.finishedAt }))

const { stepSections, hasSteps, stepDuration } = useTaskSteps(rawLinesRef, streamingRef, taskRef)
const processedLines = computed(() => props.rawLines.map(processLine))

const sectionLines = (section: StepSection) => {
  const lines = props.rawLines
    .slice(section.lineStart, section.lineEnd)
    .filter((line) => !STEP_MARKER_RE.test(line))
  const filled = lines.map((line) => Boolean(line.trim()))

  return lines.slice(filled.indexOf(true), filled.lastIndexOf(true) + 1).map(processLine)
}

const sectionHasOutput = (section: StepSection) => {
  return props.rawLines
    .slice(section.lineStart, section.lineEnd)
    .some((line) => line.trim() && !STEP_MARKER_RE.test(line))
}
</script>

<template>
  <ol v-if="hasSteps" class="p-3 sm:p-4 border border-outline-gray-1.5 rounded-5">
    <TaskStep
      v-for="section in stepSections"
      :key="section.key"
      :icon="section.icon"
      :label="section.label"
      :status="section.status"
      :duration="stepDuration(section)"
      :lines="sectionLines(section)"
      :has-output="sectionHasOutput(section)"
      :streaming="streaming && section.status === 'running'"
      :failure="section.status === 'failed' ? failure : ''"
    />
  </ol>

  <div v-else class="space-y-4">
    <Alert v-if="failure" theme="red" :title="failure" />

    <LogView :lines="processedLines" :streaming="streaming" :empty-text="emptyText" />
  </div>
</template>
