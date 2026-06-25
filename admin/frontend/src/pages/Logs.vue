<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, FormControl, LoadingText, ErrorMessage } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import { processLine } from '../utils/ansi.js'
import LucideRefreshCw from '~icons/lucide/refresh-cw'
import LucideDownload from '~icons/lucide/download'
import LucideRadio from '~icons/lucide/radio'
import LucideChevronUp from '~icons/lucide/chevron-up'
import LucideChevronDown from '~icons/lucide/chevron-down'
import LucideArrowLeft from '~icons/lucide/arrow-left'

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
const rawLines = ref([])
const contentLoading = ref(false)
const contentError = ref('')
const search = ref('')
const linesCount = ref(200)
const liveMode = ref(false)
const terminal = ref(null)
const viewer = ref(null)
const activeMatch = ref(0)   // 0-based index of the focused match
const matchTotal = ref(0)
let es = null
let lastTerm = ''

// Keep every line for context; search only highlights matches in place. Each
// match is tagged with data-mi so we can jump between them.
const visibleLines = computed(() => {
  const term = search.value.trim()
  return rawLines.value.map(l => highlight(processLine(l), term))
})

// Re-sync match navigation after the rendered lines change (search or content).
watch(visibleLines, () => nextTick(syncMatches))

function syncMatches() {
  const marks = matchEls()
  matchTotal.value = marks.length
  const term = search.value.trim()
  if (term !== lastTerm) {
    lastTerm = term
    activeMatch.value = marks.length ? 0 : -1
    paintMatches(!liveMode.value)   // scroll to first hit, except while live tailing
  } else {
    if (activeMatch.value >= marks.length) activeMatch.value = marks.length - 1
    paintMatches(false)
  }
}

function gotoMatch(delta) {
  const marks = matchEls()
  if (!marks.length) return
  activeMatch.value = (activeMatch.value + delta + marks.length) % marks.length
  paintMatches(true)
}

function matchEls() {
  return viewer.value ? [...viewer.value.querySelectorAll('mark[data-mi]')] : []
}

// Recolour the focused match and optionally scroll it into view.
function paintMatches(scroll) {
  matchEls().forEach((el, i) => {
    const active = i === activeMatch.value
    el.style.background = active ? '#fab387' : '#f9e2af'
    el.style.boxShadow = active ? '0 0 0 2px #fab387' : 'none'
    if (active && scroll) el.scrollIntoView({ block: 'center' })
  })
}

watch(selectedFile, (f) => {
  router.replace({ path: '/logs', query: f ? { file: f } : {} })
  stopLive()
  rawLines.value = []
  search.value = ''
  if (f) loadContent()
})

async function loadContent() {
  if (!selectedFile.value) return
  contentLoading.value = true
  contentError.value = ''
  try {
    const res = await fetch(`/api/logs/${selectedFile.value}?lines=${linesCount.value}`)
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    rawLines.value = d.lines
    if (!search.value.trim()) {
      await nextTick()
      terminal.value?.scrollToBottom()
    }
  } catch (e) {
    contentError.value = e.message
  } finally {
    contentLoading.value = false
  }
}

function startLive() {
  liveMode.value = true
  rawLines.value = []
  es = new EventSource(`/api/logs/${selectedFile.value}/stream`)
  es.onmessage = (e) => {
    rawLines.value.push(e.data)
    if (rawLines.value.length > 2000) rawLines.value.shift()
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
  } else if (logs.value.length && window.innerWidth >= 768) {
    // Desktop shows both panes, so preselect the first log. On mobile (< md)
    // only one pane is visible at a time — leave the list showing instead.
    selectedFile.value = logs.value[0].filename
  }
})

onUnmounted(() => { if (es) es.close() })

// Wrap matches of `term` in already-rendered HTML, touching only text between
// tags so the ANSI colour <span>s stay intact. Line text is HTML-escaped, so
// the term is escaped the same way before matching. Each match gets a running
// data-mi index used for jump-to-match navigation.
function highlight(html, term) {
  if (!term) return html
  const re = new RegExp(escapeRegExp(escapeHtml(term)), 'gi')
  return html.replace(/(<[^>]+>)|([^<]+)/g, (_, tag, text) =>
    tag || text.replace(re, m =>
      `<mark data-mi style="background:#f9e2af;color:#1e1e2e;border-radius:2px;padding:0 1px;">${m}</mark>`,
    ),
  )
}

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
</script>

<template>
  <div class="-mx-6 -my-6 flex h-full overflow-hidden">

    <!-- Left sidebar: log list — full width on mobile, hidden once a file is picked -->
    <div
      class="w-full shrink-0 border-r border-outline-gray-1 flex-col overflow-y-auto bg-surface-gray-1 md:w-52 md:flex"
      :class="selectedFile ? 'hidden' : 'flex'"
    >
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

    <!-- Right panel: viewer — full width on mobile, hidden until a file is picked -->
    <div
      class="flex-1 overflow-hidden flex-col md:flex"
      :class="selectedFile ? 'flex' : 'hidden'"
    >

      <!-- Empty state -->
      <div v-if="!selectedFile" class="flex-1 flex items-center justify-center">
        <span class="text-sm text-ink-gray-4">Select a log file</span>
      </div>

      <template v-else>
        <!-- Toolbar -->
        <div class="shrink-0 flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-5 py-2.5">
          <Button variant="ghost" class="md:hidden" tooltip="Back to logs" @click="selectedFile = ''">
            <template #icon><LucideArrowLeft class="h-4 w-4" /></template>
          </Button>
          <div class="w-32 shrink-0">
            <FormControl
              type="select"
              v-model="linesCount"
              :disabled="liveMode"
              :options="[
                { label: '100 lines', value: 100 },
                { label: '200 lines', value: 200 },
                { label: '500 lines', value: 500 },
                { label: '1000 lines', value: 1000 },
              ]"
              @change="loadContent"
            />
          </div>
          <FormControl
            type="text"
            v-model="search"
            placeholder="Search…"
            class="w-44"
            @keydown.enter.exact.prevent="gotoMatch(1)"
            @keydown.enter.shift.prevent="gotoMatch(-1)"
          />
          <div v-if="search.trim()" class="flex items-center gap-1 text-xs text-ink-gray-5">
            <span class="tabular-nums">{{ matchTotal ? activeMatch + 1 : 0 }}/{{ matchTotal }}</span>
            <Button variant="ghost" :disabled="!matchTotal" tooltip="Previous (Shift+Enter)" @click="gotoMatch(-1)">
              <template #icon><LucideChevronUp class="h-4 w-4" /></template>
            </Button>
            <Button variant="ghost" :disabled="!matchTotal" tooltip="Next (Enter)" @click="gotoMatch(1)">
              <template #icon><LucideChevronDown class="h-4 w-4" /></template>
            </Button>
          </div>

          <div class="ml-auto flex items-center gap-2">
            <Button v-if="!liveMode" variant="outline" :prefix-icon="LucideRadio" @click="startLive">
              Live tail
            </Button>
            <Button v-else variant="solid" theme="red" :prefix-icon="LucideRadio" @click="() => { stopLive(); loadContent() }">
              Stop
            </Button>
            <Button variant="outline" :prefix-icon="LucideRefreshCw" :loading="contentLoading" @click="loadContent">
              Refresh
            </Button>
            <a :href="`/api/logs/${selectedFile}/download`">
              <Button variant="ghost" tooltip="Download">
                <template #icon><LucideDownload class="h-4 w-4" /></template>
              </Button>
            </a>
          </div>
        </div>

        <!-- Terminal area -->
        <div ref="viewer" class="flex-1 overflow-hidden flex flex-col px-5 pt-4 pb-3">
          <div v-if="contentError" class="rounded-lg px-4 py-3 font-mono text-sm" style="background:#1e1e2e;">
            <span style="color:#f38ba8;">Error: {{ contentError }}</span>
          </div>
          <TerminalOutput
            v-else
            ref="terminal"
            :lines="visibleLines"
            :streaming="liveMode"
            :fill="true"
            :empty-text="contentLoading ? 'Loading…' : 'Log file is empty.'"
          />
          <div v-if="rawLines.length" class="mt-1.5 text-xs text-ink-gray-4">
            {{ rawLines.length }} lines<template v-if="search.trim()"> · {{ matchTotal }} match{{ matchTotal !== 1 ? 'es' : '' }}</template>
          </div>
        </div>
      </template>

    </div>
  </div>
</template>
