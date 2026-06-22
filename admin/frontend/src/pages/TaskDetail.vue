<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, Dialog, LoadingText, ErrorMessage } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import TaskStream from '../components/TaskStream.vue'
import { useTaskSteps } from '../composables/useTaskSteps.js'
import { processLine } from '../utils/ansi.js'
import LucideDownload from '~icons/lucide/download'
import LucideCheck from '~icons/lucide/check'
import LucideLoader2 from '~icons/lucide/loader-2'
import LucideX from '~icons/lucide/x'
import LucideChevronDown from '~icons/lucide/chevron-down'
import LucideChevronUp from '~icons/lucide/chevron-up'

const route = useRoute()
const router = useRouter()
const taskId = route.params.id

// TaskStream owns the SSE connection and output; we read its reactive state for
// the step parsing below. streamUrl stays empty until the REST load decides the
// task is still running (autoStart connects once it's set).
const taskStream = ref(null)
const streamUrl = ref('')
const rawLines = computed(() => taskStream.value?.rawLines ?? [])
const streaming = computed(() => taskStream.value?.streaming ?? false)
const task = ref(null)
const initialOutput = ref([])
const loading = ref(true)
const error = ref('')
const showKill = ref(false)
const actionLoading = ref('')
const actionError = ref('')
const expandedSteps = ref(new Set())

const TASK_COLOR = { success: 'green', failed: 'red', running: 'blue', killed: 'gray' }

function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function fmtDuration(s) {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${Math.round(s / 3600)}h`
}

const { stepSections, hasSteps, stepDuration } = useTaskSteps(rawLines, streaming, task)

function sectionLines(section) {
  return rawLines.value
    .slice(section.lineStart, section.lineEnd)
    .filter(l => !l.match(/^##\[step:/))
    .map(processLine)
}

function sectionHasOutput(section) {
  return rawLines.value
    .slice(section.lineStart, section.lineEnd)
    .some(l => l.trim() && !l.match(/^##\[step:/))
}

function toggleStep(key) {
  const next = new Set(expandedSteps.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedSteps.value = next
}

async function load() {
  try {
    const res = await fetch(`/api/tasks/${taskId}`)
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    task.value = d.task
    initialOutput.value = d.output  // seeds TaskStream on mount; streaming appends from here
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// Auto-expand the step whose output is currently arriving.
function onStreamLine(raw) {
  const m = raw.match(/^##\[step:(\w+),/)
  if (m && m[1] !== 'done') expandedSteps.value = new Set([m[1]])
}

function onStreamDone(success) {
  if (!success && stepSections.value.length) {
    // Expand the failed step so the output is immediately visible
    expandedSteps.value = new Set([stepSections.value[stepSections.value.length - 1].key])
  }
  load()
}

async function killTask() {
  showKill.value = false
  actionError.value = ''
  actionLoading.value = 'kill'
  try {
    const res = await fetch(`/api/tasks/${taskId}/kill`, { method: 'POST' })
    const d = await res.json()
    if (!d.ok) actionError.value = d.error
    else load()
  } catch (e) {
    actionError.value = e.message
  } finally {
    actionLoading.value = ''
  }
}

async function rerunTask() {
  actionError.value = ''
  actionLoading.value = 'rerun'
  try {
    const res = await fetch(`/api/tasks/${taskId}/rerun`, { method: 'POST' })
    const d = await res.json()
    if (d.ok) router.push(`/tasks/${d.task_id}`)
    else actionError.value = d.error
  } catch (e) {
    actionError.value = e.message
  } finally {
    actionLoading.value = ''
  }
}

onMounted(async () => {
  await load()
  // Setting the url makes TaskStream connect (autoStart); it seeds from
  // initialOutput first, then appends the live tail (reset disabled).
  if (task.value?.status === 'running') streamUrl.value = `/api/tasks/${taskId}/stream`
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <template v-else-if="task">
      <ErrorMessage v-if="actionError" :message="actionError" />

      <!-- Header -->
      <div class="flex flex-wrap items-center gap-3">
        <Badge
          :label="streaming ? 'running…' : task.status"
          :theme="TASK_COLOR[task.status] || 'gray'"
        />
        <span class="font-mono text-sm font-medium">{{ task.command }}</span>
        <span
          v-if="Object.keys(task.args).length"
          class="font-mono text-sm text-ink-gray-5"
        >
          {{ Object.entries(task.args).map(([k, v]) => `${k}=${v}`).join(' ') }}
        </span>
        <span class="text-sm text-ink-gray-5">{{ fmtDate(task.started_at) }}</span>
        <span v-if="task.duration_seconds != null" class="text-sm text-ink-gray-5">
          {{ fmtDuration(task.duration_seconds) }}
        </span>
        <div class="ml-auto flex gap-2">
          <a :href="`/api/tasks/${taskId}/output/download`" class="ml-auto">
            <Button variant="ghost" :prefix-icon="LucideDownload">Download</Button>
          </a>
          <Button
            v-if="task.status === 'running'"
            variant="outline"
            theme="red"
            size="sm"
            :loading="actionLoading === 'kill'"
            @click="showKill = true"
          >Kill</Button>
          <Button
            v-else
            variant="outline"
            size="sm"
            :loading="actionLoading === 'rerun'"
            @click="rerunTask"
          >Re-run</Button>
        </div>
      </div>

      <!-- TaskStream owns the SSE connection + output; we render either a
           step-grouped view or a plain terminal from its reactive state. -->
      <TaskStream
        ref="taskStream"
        :url="streamUrl"
        :reset="false"
        :initial-lines="initialOutput"
        @line="onStreamLine"
        @done="onStreamDone"
        @error="load"
      >
        <template #default="{ lines, setTerminal }">
          <!-- Multi-step view -->
          <div v-if="hasSteps" class="flex flex-col gap-1.5">
            <div v-for="section in stepSections" :key="section.key">
              <!-- Step header row -->
              <div
                class="flex items-center gap-3 rounded-lg border border-outline-gray-1 bg-surface-white px-4 py-2.5 transition-colors"
                :class="sectionHasOutput(section) ? 'cursor-pointer hover:bg-surface-gray-1' : ''"
                @click="sectionHasOutput(section) && toggleStep(section.key)"
              >
                <!-- Status icon -->
                <div
                  class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
                  :class="{
                    'bg-surface-green-2 text-ink-green-2': section.status === 'done',
                    'bg-ink-gray-9 text-surface-white': section.status === 'running',
                    'bg-surface-red-1 text-ink-red-4': section.status === 'failed',
                    'bg-ink-gray-1': section.status === 'pending',
                  }"
                >
                  <LucideCheck v-if="section.status === 'done'" class="h-3.5 w-3.5" />
                  <LucideLoader2 v-else-if="section.status === 'running'" class="h-3.5 w-3.5 animate-spin" />
                  <LucideX v-else-if="section.status === 'failed'" class="h-3.5 w-3.5" />
                  <span v-else class="h-1.5 w-1.5 rounded-full bg-ink-gray-3" />
                </div>

                <span
                  class="flex-1 text-sm"
                  :class="section.status === 'pending' ? 'text-ink-gray-4' : 'font-medium text-ink-gray-9'"
                >{{ section.label }}</span>

                <span class="text-xs text-ink-gray-5">
                  <template v-if="stepDuration(section)">{{ stepDuration(section) }}</template>
                  <span v-else-if="section.status === 'running'" class="animate-pulse">running…</span>
                </span>

                <LucideChevronUp
                  v-if="sectionHasOutput(section) && expandedSteps.has(section.key)"
                  class="h-4 w-4 shrink-0 text-ink-gray-4"
                />
                <LucideChevronDown
                  v-else-if="sectionHasOutput(section)"
                  class="h-4 w-4 shrink-0 text-ink-gray-4"
                />
              </div>

              <!-- Collapsible output -->
              <div v-if="expandedSteps.has(section.key)" class="mt-0.5 overflow-hidden rounded-b-lg">
                <TerminalOutput
                  :lines="sectionLines(section)"
                  :streaming="streaming && section.status === 'running'"
                  max-height="40vh"
                  empty-text="No output for this step."
                />
              </div>
            </div>
          </div>

          <!-- Plain terminal (tasks without steps) -->
          <TerminalOutput
            v-else
            :ref="setTerminal"
            :lines="lines"
            :streaming="streaming"
            :line-numbers="true"
            empty-text="No output yet…"
            max-height="calc(100vh - 200px)"
          />
        </template>
      </TaskStream>
    </template>

    <Dialog v-model="showKill" :options="{ title: 'Kill Task', size: 'sm' }">
      <template #body-content>
        <p class="text-ink-gray-5">Send SIGTERM to the running process?</p>
        <div class="mt-4 flex justify-end gap-2">
          <Button variant="ghost" @click="showKill = false">Cancel</Button>
          <Button variant="solid" theme="red" @click="killTask">Kill</Button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
