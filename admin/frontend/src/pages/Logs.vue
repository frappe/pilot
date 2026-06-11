<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, FormControl, LoadingText, ErrorMessage } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import { processLine } from '../utils/ansi.js'
import LucideRefreshCw from '~icons/lucide/refresh-cw'
import LucideDownload from '~icons/lucide/download'
import LucideRadio from '~icons/lucide/radio'

const route = useRoute()
const router = useRouter()

// ── Log list ──────────────────────────────────────────────────────────────────
const logs = ref([])
const logsLoading = ref(true)
const logsError = ref('')

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function loadLogs() {
  logsLoading.value = true
  logsError.value = ''
  try {
    const res = await fetch('/api/logs/')
    logs.value = await res.json()
  } catch (e) {
    logsError.value = e.message
  } finally {
    logsLoading.value = false
  }
}

// ── Viewer ────────────────────────────────────────────────────────────────────
const selectedFile = ref(route.query.file || '')
const lines = ref([])
const contentLoading = ref(false)
const contentError = ref('')
const search = ref('')
const linesCount = ref(200)
const liveMode = ref(false)
const terminal = ref(null)
let es = null

watch(selectedFile, (f) => {
  router.replace({ path: '/logs', query: f ? { file: f } : {} })
  stopLive()
  lines.value = []
  search.value = ''
  if (f) loadContent()
})

async function loadContent() {
  if (!selectedFile.value) return
  contentLoading.value = true
  contentError.value = ''
  try {
    const params = new URLSearchParams({ lines: linesCount.value })
    if (search.value) params.set('search', search.value)
    const res = await fetch(`/api/logs/${selectedFile.value}?${params}`)
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    lines.value = d.lines.map(processLine)
  } catch (e) {
    contentError.value = e.message
  } finally {
    contentLoading.value = false
  }
}

function startLive() {
  liveMode.value = true
  lines.value = []
  es = new EventSource(`/api/logs/${selectedFile.value}/stream`)
  es.onmessage = (e) => {
    lines.value.push(processLine(e.data))
    if (lines.value.length > 2000) lines.value.shift()
    terminal.value?.scrollToBottom()
  }
  es.onerror = () => stopLive()
}

function stopLive() {
  liveMode.value = false
  if (es) { es.close(); es = null }
}

onMounted(async () => {
  await loadLogs()
  if (selectedFile.value) {
    loadContent()
  } else if (logs.value.length) {
    selectedFile.value = logs.value[0].filename
  }
})

onUnmounted(() => { if (es) es.close() })
</script>

<template>
  <div class="-mx-6 -my-6 flex h-full overflow-hidden">

    <!-- Left sidebar: log list -->
    <div class="w-52 shrink-0 border-r border-outline-gray-1 flex flex-col overflow-y-auto bg-surface-gray-1">
      <LoadingText v-if="logsLoading" class="p-4" />
      <ErrorMessage v-else-if="logsError" :message="logsError" class="p-3" />
      <button
        v-else
        v-for="log in logs"
        :key="log.filename"
        class="w-full text-left px-3 py-2.5 border-b border-outline-gray-1 transition-colors hover:bg-surface-gray-2"
        :class="selectedFile === log.filename
          ? 'bg-surface-white border-l-2 border-l-ink-gray-7'
          : 'border-l-2 border-l-transparent'"
        @click="selectedFile = log.filename"
      >
        <div class="text-sm font-medium text-ink-gray-8 truncate">{{ log.process_name || log.filename }}</div>
        <div class="mt-0.5 text-xs text-ink-gray-4">{{ fmtSize(log.size_bytes) }}</div>
      </button>
    </div>

    <!-- Right panel: viewer -->
    <div class="flex-1 overflow-hidden flex flex-col">

      <!-- Empty state -->
      <div v-if="!selectedFile" class="flex-1 flex items-center justify-center">
        <span class="text-sm text-ink-gray-4">Select a log file</span>
      </div>

      <template v-else>
        <!-- Toolbar -->
        <div class="shrink-0 flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-5 py-2.5">
          <FormControl
            type="text"
            v-model="search"
            placeholder="Search…"
            class="w-44"
            @keyup.enter="loadContent"
          />
          <FormControl
            type="select"
            v-model="linesCount"
            class="w-36"
            :options="[
              { label: 'Last 100 lines', value: 100 },
              { label: 'Last 200 lines', value: 200 },
              { label: 'Last 500 lines', value: 500 },
              { label: 'Last 1000 lines', value: 1000 },
            ]"
            @change="loadContent"
          />
          <Button variant="outline" :prefix-icon="LucideRefreshCw" :loading="contentLoading" @click="loadContent">
            Refresh
          </Button>
          <Button v-if="!liveMode" variant="outline" :prefix-icon="LucideRadio" @click="startLive">
            Live tail
          </Button>
          <Button v-else variant="solid" theme="red" :prefix-icon="LucideRadio" @click="() => { stopLive(); loadContent() }">
            Stop
          </Button>
          <a :href="`/api/logs/${selectedFile}/download`" class="ml-auto">
            <Button variant="ghost" :prefix-icon="LucideDownload">Download</Button>
          </a>
        </div>

        <!-- Terminal area -->
        <div class="flex-1 overflow-hidden flex flex-col px-5 pt-4 pb-3">
          <div v-if="contentError" class="rounded-lg px-4 py-3 font-mono text-sm" style="background:#1e1e2e;">
            <span style="color:#f38ba8;">Error: {{ contentError }}</span>
          </div>
          <TerminalOutput
            v-else
            ref="terminal"
            :lines="lines"
            :streaming="liveMode"
            :fill="true"
            :empty-text="contentLoading ? 'Loading…' : search ? 'No lines match your search.' : 'Log file is empty.'"
          />
          <div v-if="lines.length" class="mt-1.5 text-xs text-ink-gray-4">
            {{ lines.length }} line{{ lines.length !== 1 ? 's' : '' }}
          </div>
        </div>
      </template>

    </div>
  </div>
</template>
