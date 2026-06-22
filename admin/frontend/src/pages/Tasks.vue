<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, Dialog, FormControl, LoadingText, ErrorMessage } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import { useTaskSteps } from '../composables/useTaskSteps.js'
import { processLine } from '../utils/ansi.js'
import LucideCheck from '~icons/lucide/check'
import LucideLoader2 from '~icons/lucide/loader-2'
import LucideX from '~icons/lucide/x'
import LucideChevronDown from '~icons/lucide/chevron-down'
import LucideChevronUp from '~icons/lucide/chevron-up'

const route = useRoute()
const router = useRouter()

// ── Task list ─────────────────────────────────────────────────────────────────
const tasks = ref([])
const tasksLoading = ref(true)
const tasksError = ref('')
const statusFilter = ref('all')

const filterOptions = [
  { label: 'All tasks', value: 'all' },
  { label: 'Running', value: 'running' },
  { label: 'Success', value: 'success' },
  { label: 'Failed', value: 'failed' },
  { label: 'Killed', value: 'killed' },
]

const TASK_COLOR = { success: 'green', failed: 'red', running: 'blue', killed: 'gray' }
const TASK_DOT = {
  success: 'bg-surface-green-3',
  failed: 'bg-surface-red-4',
  running: 'bg-ink-gray-9',
  killed: 'bg-ink-gray-3',
}

function fmtArgs(args) {
  if (!args || !Object.keys(args).length) return ''
  const parts = []
  if (args.site) parts.push(args.site)
  if (args.app) parts.push(args.app)
  if (args.name) parts.push(args.name)
  if (args.repo) parts.push(args.repo)
  return parts.join(' · ') || Object.values(args).join(' · ')
}

function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function fmtDateShort(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const now = new Date()
  if (d.toDateString() === now.toDateString())
    return d.toLocaleTimeString(undefined, { timeStyle: 'short' })
  return d.toLocaleDateString(undefined, { dateStyle: 'short' })
}

function fmtDuration(s) {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${Math.round(s / 3600)}h`
}

async function loadTasks() {
  tasksError.value = ''
  try {
    const params = statusFilter.value !== 'all' ? `?status=${statusFilter.value}` : ''
    const res = await fetch(`/api/tasks/${params}`)
    tasks.value = await res.json()
  } catch (e) {
    tasksError.value = e.message
  } finally {
    tasksLoading.value = false
  }
}

// ── Task detail ───────────────────────────────────────────────────────────────
const selectedTaskId = ref(route.query.task || '')
const task = ref(null)
const rawLines = ref([])
const lines = ref([])
const detailLoading = ref(false)
const detailError = ref('')
const streaming = ref(false)
const showKill = ref(false)
const actionLoading = ref('')
const actionError = ref('')
const expandedSteps = ref(new Set())
const terminal = ref(null)
let es = null

watch(selectedTaskId, (id) => {
  router.replace({ path: '/tasks', query: id ? { task: id } : {} })
  closeStream()
  task.value = null
  rawLines.value = []
  lines.value = []
  if (id) loadDetail(id)
})

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

async function loadDetail(id) {
  detailLoading.value = true
  detailError.value = ''
  actionError.value = ''
  expandedSteps.value = new Set()
  try {
    const res = await fetch(`/api/tasks/${id}`)
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    task.value = d.task
    rawLines.value = d.output
    lines.value = d.output.map(processLine)
  } catch (e) {
    detailError.value = e.message
  } finally {
    detailLoading.value = false
    if (task.value?.status === 'running') startStream(id)
  }
}

function startStream(id) {
  streaming.value = true
  let volatile = false  // last line is an uncommitted \r progress preview
  es = new EventSource(`/api/tasks/${id}/stream`)
  es.onmessage = (e) => {
    const raw = e.data
    if (volatile) { rawLines.value.pop(); lines.value.pop(); volatile = false }
    rawLines.value.push(raw)
    lines.value.push(processLine(raw))
    terminal.value?.scrollToBottom()
  }
  es.addEventListener('overwrite', (e) => {
    const raw = e.data
    if (volatile) {
      rawLines.value[rawLines.value.length - 1] = raw
      lines.value[lines.value.length - 1] = processLine(raw)
    } else {
      rawLines.value.push(raw)
      lines.value.push(processLine(raw))
      volatile = true
    }
    terminal.value?.scrollToBottom()
  })
  es.addEventListener('done', () => {
    streaming.value = false
    es.close(); es = null
    loadDetail(id)
    loadTasks()
  })
  es.onerror = () => {
    streaming.value = false
    if (es) { es.close(); es = null }
  }
}

function closeStream() {
  streaming.value = false
  if (es) { es.close(); es = null }
}

async function killTask() {
  showKill.value = false
  actionError.value = ''
  actionLoading.value = 'kill'
  try {
    const res = await fetch(`/api/tasks/${selectedTaskId.value}/kill`, { method: 'POST' })
    const d = await res.json()
    if (!d.ok) actionError.value = d.error
    else { loadDetail(selectedTaskId.value); loadTasks() }
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
    const res = await fetch(`/api/tasks/${selectedTaskId.value}/rerun`, { method: 'POST' })
    const d = await res.json()
    if (d.ok) {
      await loadTasks()
      selectedTaskId.value = d.task_id
    } else {
      actionError.value = d.error
    }
  } catch (e) {
    actionError.value = e.message
  } finally {
    actionLoading.value = ''
  }
}

let pollTimer = null

onMounted(async () => {
  await loadTasks()
  if (selectedTaskId.value) {
    loadDetail(selectedTaskId.value)
  } else if (tasks.value.length) {
    selectedTaskId.value = tasks.value[0].task_id
  }
  pollTimer = setInterval(loadTasks, 5000)
})

onUnmounted(() => {
  closeStream()
  clearInterval(pollTimer)
})
</script>

<template>
  <div class="-mx-6 -my-6 flex h-full overflow-hidden">

    <!-- Left sidebar: task list -->
    <div class="w-64 shrink-0 border-r border-outline-gray-1 flex flex-col overflow-hidden bg-surface-gray-1">
      <div class="shrink-0 border-b border-outline-gray-1 px-3 py-2">
        <FormControl
          type="select"
          v-model="statusFilter"
          :options="filterOptions"
          @change="loadTasks"
        />
      </div>
      <div class="flex-1 overflow-y-auto">
        <LoadingText v-if="tasksLoading" class="p-4" />
        <ErrorMessage v-else-if="tasksError" :message="tasksError" class="p-3" />
        <p v-else-if="!tasks.length" class="p-4 text-center text-sm text-ink-gray-4">No tasks.</p>
        <button
          v-else
          v-for="t in tasks"
          :key="t.task_id"
          class="w-full text-left px-3 py-2.5 border-b border-outline-gray-1 transition-colors hover:bg-surface-gray-2"
          :class="selectedTaskId === t.task_id
            ? 'bg-surface-white border-l-2 border-l-ink-gray-7'
            : 'border-l-2 border-l-transparent'"
          @click="selectedTaskId = t.task_id"
        >
          <div class="flex items-center gap-2">
            <span class="h-2 w-2 shrink-0 rounded-full" :class="TASK_DOT[t.status] || 'bg-ink-gray-3'" />
            <span class="text-sm font-medium text-ink-gray-8 truncate">{{ t.command }}</span>
          </div>
          <div class="mt-0.5 flex items-center justify-between gap-2">
            <span class="text-xs text-ink-gray-4 truncate">{{ fmtArgs(t.args) || '—' }}</span>
            <span class="text-xs text-ink-gray-4 shrink-0">{{ fmtDateShort(t.started_at) }}</span>
          </div>
        </button>
      </div>
    </div>

    <!-- Right panel: detail -->
    <div class="flex-1 overflow-hidden flex flex-col">

      <div v-if="!selectedTaskId" class="flex-1 flex items-center justify-center">
        <span class="text-sm text-ink-gray-4">Select a task</span>
      </div>

      <template v-else>
        <LoadingText v-if="detailLoading" class="p-6" />
        <template v-else-if="task">

          <!-- Header -->
          <div class="shrink-0 border-b border-outline-gray-1 px-5 py-3 flex flex-wrap items-center gap-3">
            <Badge
              :label="streaming ? 'running…' : task.status"
              :theme="TASK_COLOR[task.status] || 'gray'"
            />
            <span class="font-mono text-sm font-medium">{{ task.command }}</span>
            <span v-if="Object.keys(task.args).length" class="font-mono text-sm text-ink-gray-5">
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

          <ErrorMessage v-if="actionError" :message="actionError" class="mx-5 mt-3" />
          <ErrorMessage v-else-if="detailError" :message="detailError" class="mx-5 mt-3" />

          <!-- Steps view -->
          <div v-if="hasSteps" class="flex-1 overflow-auto flex flex-col px-5 py-4 gap-1.5">
            <div v-for="section in stepSections" :key="section.key">
              <div
                class="flex items-center gap-3 rounded-lg border border-outline-gray-1 bg-surface-white px-4 py-2.5 transition-colors"
                :class="sectionHasOutput(section) ? 'cursor-pointer hover:bg-surface-gray-1' : ''"
                @click="sectionHasOutput(section) && toggleStep(section.key)"
              >
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

          <!-- Plain terminal -->
          <div v-else class="flex-1 overflow-hidden flex flex-col px-5 py-4">
            <TerminalOutput
              ref="terminal"
              :lines="lines"
              :streaming="streaming"
              :line-numbers="true"
              :fill="true"
              empty-text="No output yet…"
            />
          </div>

        </template>
      </template>
    </div>

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
