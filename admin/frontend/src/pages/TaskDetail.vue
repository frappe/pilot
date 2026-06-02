<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, Dialog, LoadingText, ErrorMessage } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import { processLine } from '../utils/ansi.js'

const route = useRoute()
const router = useRouter()
const taskId = route.params.id

const task = ref(null)
const lines = ref([])
const loading = ref(true)
const error = ref('')
const streaming = ref(false)
const showKill = ref(false)
const actionLoading = ref('')
const actionError = ref('')
let es = null
const terminal = ref(null)

const TASK_COLOR = { success: 'green', failed: 'red', running: 'blue', killed: 'gray', stopped: 'red' }

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

async function load() {
  try {
    const res = await fetch(`/api/tasks/${taskId}`)
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    task.value = d.task
    lines.value = d.output.map(processLine)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function startStream() {
  streaming.value = true
  es = new EventSource(`/api/tasks/${taskId}/stream`)
  es.onmessage = (e) => {
    lines.value.push(processLine(e.data))
    terminal.value?.scrollToBottom()
  }
  es.addEventListener('done', () => {
    streaming.value = false
    es.close(); es = null
    load()
  })
  es.onerror = () => {
    streaming.value = false
    if (es) { es.close(); es = null }
  }
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
onUnmounted(() => { if (es) { es.close(); es = null } })
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
          :theme="streaming ? 'blue' : (TASK_COLOR[task.status] || 'gray')"
        >
          <template v-if="streaming" #prefix>
            <span class="pulse-dot" />
          </template>
        </Badge>
        <span class="font-mono text-sm font-medium">{{ task.command }}</span>
        <span
          v-if="Object.keys(task.args).length"
          class="font-mono text-sm"
          style="color:#585b70;"
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

      <!-- Output -->
      <TerminalOutput
        ref="terminal"
        :lines="lines"
        :streaming="streaming"
        :line-numbers="true"
        empty-text="No output yet…"
        max-height="calc(100vh - 200px)"
      />
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
