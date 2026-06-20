<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { Button } from 'frappe-ui'
import { useTaskProgress } from '../composables/useTaskProgress.js'
import LucideCheck from '~icons/lucide/check'
import LucideLoader2 from '~icons/lucide/loader-2'
import LucideX from '~icons/lucide/x'
import LucideMinus from '~icons/lucide/minus'
import LucideMaximize2 from '~icons/lucide/maximize-2'

const { activeTaskId, clearTask } = useTaskProgress()

const task = ref(null)
const rawLines = ref([])
const streaming = ref(false)
const minimized = ref(false)
let es = null

// ── step parsing ──────────────────────────────────────────────────────────────
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
    const endedAt = next ? next.ts : null
    let status
    if (next) status = 'done'
    else if (!streaming.value && task.value?.status === 'failed') status = 'failed'
    else if (!streaming.value) status = 'done'
    else status = 'running'
    sections.push({ key: m.key, label: m.label, startedAt: m.ts, endedAt, status })
  }
  return sections
})

const hasSteps = computed(() => stepSections.value.length > 0)

const progressPct = computed(() => {
  if (!hasSteps.value) return null
  const done = stepSections.value.filter(s => s.status === 'done').length
  return Math.round((done / stepSections.value.length) * 100)
})

function stepDuration(section) {
  if (!section.startedAt || !section.endedAt) return null
  const s = (section.endedAt - section.startedAt) / 1000
  return s < 60 ? `${s.toFixed(1)}s` : `${(s / 60).toFixed(1)}m`
}

// ── live status line ──────────────────────────────────────────────────────────
const NOISY_LINE = /^(##\[step:|━+|─+|\s*[-=]{4,}|\s*\^+|Traceback|  File "|During handling|\s*\|)/

function cleanLine(raw) {
  const s = raw.replace(/\x1B\[[0-9;]*m/g, '').trim()
  if (!s || s.length < 4 || NOISY_LINE.test(s)) return null
  return s
}

const currentStatus = computed(() => {
  if (!streaming.value) return null
  // For step tasks: label of the currently running step
  if (hasSteps.value) {
    return stepSections.value.find(s => s.status === 'running')?.label ?? null
  }
  // For step-less tasks: latest readable line from the stream
  for (let i = rawLines.value.length - 1; i >= 0; i--) {
    const clean = cleanLine(rawLines.value[i])
    if (clean) return clean
  }
  return null
})

// ── error extraction ──────────────────────────────────────────────────────────
const friendlyError = computed(() => {
  if (task.value?.status !== 'failed') return null
  const boilerplate = /^(Traceback \(most recent call last\)|  File "|    |  \^|During handling)/
  for (let i = rawLines.value.length - 1; i >= 0; i--) {
    const line = rawLines.value[i].trim()
    if (!line || boilerplate.test(line)) continue
    return line.replace(/\x1B\[[0-9;]*m/g, '')
  }
  return 'Task failed. Check the task log for details.'
})

// ── task label ────────────────────────────────────────────────────────────────
function readableCommand(cmd) {
  if (!cmd) return 'Task'
  return cmd.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
}

const taskLabel = computed(() => {
  if (!task.value) return 'Running…'
  return readableCommand(task.value.command)
})

const taskSubLabel = computed(() => {
  if (!task.value) return ''
  const args = task.value.args || {}
  const parts = []
  if (args.site) parts.push(args.site)
  if (args.app) parts.push(args.app)
  if (args.name) parts.push(args.name)
  return parts.join(' · ')
})

const isDone = computed(() => task.value && !streaming.value && task.value.status !== 'running')

// ── streaming ─────────────────────────────────────────────────────────────────
function startStream(id) {
  streaming.value = true
  let volatile = false
  es = new EventSource(`/api/tasks/${id}/stream`)
  es.onmessage = (e) => {
    const raw = e.data
    if (volatile) { rawLines.value.pop(); volatile = false }
    rawLines.value.push(raw)
  }
  es.addEventListener('overwrite', (e) => {
    const raw = e.data
    if (volatile) {
      rawLines.value[rawLines.value.length - 1] = raw
    } else {
      rawLines.value.push(raw)
      volatile = true
    }
  })
  es.addEventListener('done', () => {
    streaming.value = false
    es.close(); es = null
    loadTask(activeTaskId.value)
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

async function loadTask(id) {
  if (!id) return
  try {
    const res = await fetch(`/api/tasks/${id}`)
    if (!res.ok) return
    const d = await res.json()
    task.value = d.task
    rawLines.value = d.output
    if (d.task.status === 'running') startStream(id)
  } catch { /* silent */ }
}

watch(activeTaskId, (id) => {
  if (!id) return
  closeStream()
  task.value = null
  rawLines.value = []
  minimized.value = false
  loadTask(id)
})

function dismiss() {
  const succeeded = task.value?.status === 'success'
  closeStream()
  clearTask()
  task.value = null
  rawLines.value = []
  minimized.value = false
  if (succeeded) window.location.reload()
}

onUnmounted(closeStream)
</script>

<template>
  <Teleport to="body">
    <!-- Full modal -->
    <div
      v-if="activeTaskId && !minimized"
      class="fixed inset-0 z-[100] flex items-center justify-center"
    >
      <div class="absolute inset-0 bg-black/30" />

      <div class="relative z-10 flex w-[480px] flex-col rounded-xl bg-surface-white shadow-2xl ring-1 ring-outline-gray-2">

        <!-- Header -->
        <div class="flex shrink-0 items-center gap-3 border-b border-outline-gray-1 px-5 py-3.5">
          <div class="flex-1 min-w-0">
            <p class="truncate text-base font-semibold text-ink-gray-9">{{ taskLabel }}</p>
            <p v-if="taskSubLabel" class="truncate text-xs text-ink-gray-5 mt-0.5">{{ taskSubLabel }}</p>
          </div>

          <button
            class="flex h-6 w-6 items-center justify-center rounded text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-gray-7 transition-colors"
            @click="minimized = true"
          >
            <LucideMinus class="h-3.5 w-3.5" />
          </button>
        </div>

        <!-- Body -->
        <div class="p-5 flex flex-col gap-4">

          <!-- Progress bar: only shown for tasks with steps -->
          <div v-if="hasSteps" class="overflow-hidden rounded-full h-2 bg-surface-gray-2">
            <div
              class="h-full rounded-full transition-all duration-500"
              :class="task?.status === 'failed' ? 'bg-surface-red-4' : task?.status === 'success' ? 'bg-surface-green-3' : 'bg-ink-gray-8'"
              :style="{ width: (progressPct ?? 0) + '%' }"
            />
          </div>

          <!-- Error callout -->
          <div
            v-if="friendlyError"
            class="rounded-lg bg-surface-red-1 border border-outline-red-1 px-4 py-3"
          >
            <p class="text-sm font-medium text-ink-red-4">Task failed</p>
            <p class="mt-0.5 text-sm text-ink-red-3 break-words">{{ friendlyError }}</p>
          </div>

          <!-- Status for step-less tasks -->
          <div v-if="!hasSteps" class="flex flex-col items-center justify-center gap-3 py-8">
            <template v-if="streaming || task?.status === 'running'">
              <LucideLoader2 class="h-8 w-8 animate-spin text-ink-gray-4" />
              <p class="text-sm text-ink-gray-5 text-center max-w-xs truncate">{{ currentStatus || 'Working…' }}</p>
            </template>
            <template v-else-if="task?.status === 'success'">
              <div class="flex h-12 w-12 items-center justify-center rounded-full bg-surface-green-2">
                <LucideCheck class="h-6 w-6 text-ink-green-2" />
              </div>
              <p class="text-sm text-ink-gray-6">Completed successfully</p>
            </template>
            <template v-else-if="task?.status === 'failed'">
              <div class="flex h-12 w-12 items-center justify-center rounded-full bg-surface-red-1">
                <LucideX class="h-6 w-6 text-ink-red-4" />
              </div>
            </template>
          </div>

          <!-- Step list -->
          <div v-if="hasSteps" class="flex flex-col gap-1">
            <p
              v-if="currentStatus"
              class="px-3 pb-1 text-xs text-ink-gray-4 truncate"
            >{{ currentStatus }}</p>
            <div
              v-for="section in stepSections"
              :key="section.key"
              class="flex items-center gap-3 rounded-lg px-3 py-2"
            >
              <div
                class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
                :class="{
                  'bg-surface-green-2 text-ink-green-2': section.status === 'done',
                  'bg-ink-gray-9 text-surface-white': section.status === 'running',
                  'bg-surface-red-1 text-ink-red-4': section.status === 'failed',
                  'bg-surface-gray-3': section.status === 'pending',
                }"
              >
                <LucideCheck v-if="section.status === 'done'" class="h-3 w-3" />
                <LucideLoader2 v-else-if="section.status === 'running'" class="h-3 w-3 animate-spin" />
                <LucideX v-else-if="section.status === 'failed'" class="h-3 w-3" />
                <span v-else class="h-1.5 w-1.5 rounded-full bg-ink-gray-3" />
              </div>
              <span
                class="flex-1 text-sm"
                :class="section.status === 'pending' ? 'text-ink-gray-4' : 'text-ink-gray-9'"
              >{{ section.label }}</span>
              <span class="text-xs text-ink-gray-4 shrink-0">
                <template v-if="stepDuration(section)">{{ stepDuration(section) }}</template>
                <span v-else-if="section.status === 'running'" class="animate-pulse">running…</span>
              </span>
            </div>
          </div>
        </div>

        <!-- Footer: only when done -->
        <div
          v-if="isDone"
          class="shrink-0 flex justify-end border-t border-outline-gray-1 px-5 py-3"
        >
          <Button variant="solid" @click="dismiss">Close</Button>
        </div>
      </div>
    </div>

    <!-- Minimized pill -->
    <div
      v-else-if="activeTaskId && minimized"
      class="fixed bottom-4 right-4 z-[100] flex cursor-pointer items-center gap-3 rounded-xl bg-surface-white px-4 py-3 shadow-xl ring-1 ring-outline-gray-2 transition-shadow hover:shadow-2xl"
      @click="minimized = false"
    >
      <div
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
        :class="{
          'bg-ink-gray-9': streaming || task?.status === 'running',
          'bg-surface-green-2': task?.status === 'success',
          'bg-surface-red-1': task?.status === 'failed',
          'bg-surface-gray-2': !task,
        }"
      >
        <LucideLoader2
          v-if="streaming || task?.status === 'running' || !task"
          class="h-3.5 w-3.5 text-surface-white animate-spin"
        />
        <LucideCheck v-else-if="task?.status === 'success'" class="h-3.5 w-3.5 text-ink-green-2" />
        <LucideX v-else-if="task?.status === 'failed'" class="h-3.5 w-3.5 text-ink-red-4" />
      </div>

      <div class="min-w-0">
        <p class="max-w-[220px] truncate text-sm font-medium text-ink-gray-9">{{ taskLabel }}</p>
        <p v-if="taskSubLabel" class="max-w-[220px] truncate text-xs text-ink-gray-4">{{ taskSubLabel }}</p>
        <p class="max-w-[220px] truncate text-xs text-ink-gray-5">
          <template v-if="streaming || task?.status === 'running'">{{ currentStatus || 'Running…' }}</template>
          <template v-else-if="task?.status === 'success'">Completed</template>
          <template v-else-if="task?.status === 'failed'">Failed</template>
          <template v-else>Starting…</template>
        </p>
      </div>

      <LucideMaximize2 class="h-3.5 w-3.5 shrink-0 text-ink-gray-4" />
    </div>
  </Teleport>
</template>

