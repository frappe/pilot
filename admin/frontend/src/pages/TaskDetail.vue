<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, Dialog, LoadingText, ErrorMessage } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import { processLine } from '../utils/ansi.js'
import { useTaskStream } from '../composables/useTaskStream.js'
import LucideCheck from '~icons/lucide/check'
import LucideLoader2 from '~icons/lucide/loader-2'
import LucideX from '~icons/lucide/x'
import LucideChevronDown from '~icons/lucide/chevron-down'
import LucideChevronUp from '~icons/lucide/chevron-up'

const route = useRoute()
const router = useRouter()
const taskId = route.params.id

const { terminal, lines, rawLines, streaming, start } = useTaskStream()
const task = ref(null)
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

// Parse ##[step:KEY,TIMESTAMP] markers into sections with line ranges
const stepSections = computed(() => {
  const markers = []
  rawLines.value.forEach((line, idx) => {
    const m = line.match(/^##\[step:(\w+),([\d.]+)\]\s*(.*)/)
    if (m) markers.push({ key: m[1], ts: parseFloat(m[2]) * 1000, label: m[3].trim(), idx })
  })

  const sections = []
  for (let i = 0; i < markers.length; i++) {
    const m = markers[i]
    if (m.key === 'done') break

    const next = markers[i + 1]
    const lineStart = m.idx + 1
    const lineEnd = next ? next.idx : rawLines.value.length
    const endedAt = next ? next.ts : null

    let status
    if (next) {
      status = 'done'
    } else if (!streaming.value && task.value?.status === 'failed') {
      status = 'failed'
    } else if (!streaming.value) {
      status = 'done'
    } else {
      status = 'running'
    }

    sections.push({ key: m.key, label: m.label, startedAt: m.ts, endedAt, lineStart, lineEnd, status })
  }
  return sections
})

const hasSteps = computed(() => stepSections.value.length > 0)

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

function stepDuration(section) {
  if (!section.startedAt || !section.endedAt) return null
  const s = (section.endedAt - section.startedAt) / 1000
  return s < 60 ? `${s.toFixed(1)}s` : `${(s / 60).toFixed(1)}m`
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
    rawLines.value = d.output
    lines.value = d.output.map(processLine)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function startStream() {
  start(`/api/tasks/${taskId}/stream`, {
    onLine: (raw) => {
      const m = raw.match(/^##\[step:(\w+),/)
      if (m && m[1] !== 'done') expandedSteps.value = new Set([m[1]])
    },
    onDone: (success) => {
      if (!success && stepSections.value.length) {
        // Expand the failed step so the output is immediately visible
        expandedSteps.value = new Set([stepSections.value[stepSections.value.length - 1].key])
      }
      load()
    },
    onError: () => load(),
  })
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
  if (task.value?.status === 'running') startStream()
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

      <!-- Multi-step view -->
      <template v-if="hasSteps">
        <div class="flex flex-col gap-1.5">
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
      </template>

      <!-- Plain terminal (tasks without steps) -->
      <template v-else>
        <TerminalOutput
          ref="terminal"
          :lines="lines"
          :streaming="streaming"
          :line-numbers="true"
          empty-text="No output yet…"
          max-height="calc(100vh - 200px)"
        />
      </template>
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
