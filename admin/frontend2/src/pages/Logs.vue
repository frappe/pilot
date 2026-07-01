<template>
  <UpdatesAvailableButton />

  <div class="flex flex-col h-full">
    <!-- Header: hidden on mobile once a log is open, to leave more room for the viewer -->
    <div class="mb-4 shrink-0" :class="selectedFile ? 'hidden md:block' : ''">
      <h1 class="font-semibold text-ink-gray-9 text-xl">Logs</h1>
      <p class="mt-1 text-ink-gray-5 text-sm">Output from {{ benchName }}'s services.</p>
    </div>

    <div class="flex flex-1 sm:gap-4 -mx-6 sm:mx-0 min-h-0">
      <!-- Sidebar: log list -->
      <div
        class="md:flex flex-col sm:border sm:rounded-xl sm:border-outline-gray-2 w-full md:w-64 overflow-hidden shrink-0"
        :class="selectedFile ? 'hidden' : 'flex'">
        <div class="p-2 border-b border-outline-gray-2 shrink-0">
          <FormControl type="text" v-model="fileSearch" placeholder="Search log files" />
        </div>
        <div class="flex-1 p-1.5 sm:p-2 overflow-y-auto">
          <LoadingText v-if="logsLoading" class="p-2" />
          <ErrorMessage v-else-if="logsError" :message="logsError" class="p-2" />
          <p v-else-if="!filteredLogs.length" class="p-2 text-ink-gray-4 text-sm">No log files found.</p>
          <button v-else v-for="log in filteredLogs" :key="log.filename"
            class="px-3 py-2.5 rounded-lg w-full text-left transition-colors"
            :class="selectedFile === log.filename ? 'bg-surface-gray-2' : 'hover:bg-surface-gray-1'"
            @click="selectedFile = log.filename">
            <div class="flex justify-between items-center gap-2">
              <span class="font-medium text-ink-gray-8 text-sm truncate">{{ log.filename }}</span>
              <span v-if="hasErrors(log)" class="bg-red-500 rounded-full size-1.5 shrink-0" />
            </div>
            <div class="flex justify-between items-center mt-0.5 text-ink-gray-4 text-xs">
              <span>{{ formatBytes(log.size_bytes) }}</span>
              <span>{{ shortRelativeTime(log.last_modified) }}</span>
            </div>
          </button>
        </div>
      </div>

      <!-- Viewer -->
      <div class="md:flex flex-col flex-1 sm:border sm:rounded-xl sm:border-outline-gray-2 overflow-hidden"
        :class="selectedFile ? 'flex' : 'hidden'">
        <div v-if="!selectedFile" class="flex flex-1 justify-center items-center">
          <span class="text-ink-gray-4 text-sm">Select a log file</span>
        </div>

        <template v-else>
          <!-- Toolbar -->
          <div
            class="flex sm:flex-row flex-col sm:flex-wrap sm:items-center gap-2 p-2 border-b border-outline-gray-2 shrink-0">
            <!-- Mobile-only: back + filename, replacing the standalone filename bar -->
            <div class="md:hidden flex items-center gap-2">
              <Button variant="subtle" tooltip="Back to logs" @click="selectedFile = ''">
                <span class="lucide-arrow-left size-4" />
              </Button>
              <span class="flex-1 min-w-0 font-mono text-ink-gray-8 text-sm truncate">{{ truncateFilename(selectedFile)
              }}</span>
            </div>
            <div class="flex items-center gap-2">
              <div class="w-28 sm:w-32 min-w-0 shrink-0">
                <FormControl type="select" v-model="linesCount" :disabled="liveMode" :options="[
                  { label: '100 lines', value: 100 },
                  { label: '200 lines', value: 200 },
                  { label: '500 lines', value: 500 },
                  { label: '1000 lines', value: 1000 },
                ]" />
              </div>
              <FormControl type="text" v-model="search" placeholder="Search this log…"
                class="flex-1 sm:flex-none sm:w-44 min-w-0" @keydown.enter.exact.prevent="gotoMatch(1)"
                @keydown.enter.shift.prevent="gotoMatch(-1)" />
            </div>
            <div v-if="search.trim()" class="hidden sm:flex items-center gap-1 text-ink-gray-5 text-xs">
              <span class="tabular-nums">{{ matchTotal ? activeMatch + 1 : 0 }}/{{ matchTotal }}</span>
              <Button variant="subtle" :disabled="!matchTotal" tooltip="Previous (Shift+Enter)" @click="gotoMatch(-1)">
                <span class="size-4 lucide-chevron-up" />
              </Button>
              <Button variant="subtle" :disabled="!matchTotal" tooltip="Next (Enter)" @click="gotoMatch(1)">
                <span class="size-4 lucide-chevron-down" />
              </Button>
            </div>

            <div class="sm:flex sm:items-center gap-2 grid grid-cols-3 sm:ml-auto sm:w-auto">
              <Button variant="subtle" class="w-full sm:w-auto" icon-left="lucide-refresh-cw" :loading="contentLoading"
                @click="loadContent">
                Refresh
              </Button>
              <Button v-if="!liveMode" variant="subtle" class="w-full sm:w-auto" icon-left="lucide-radio"
                @click="startLive">
                Live tail
              </Button>
              <Button v-else variant="subtle" theme="red" class="w-full sm:w-auto" icon-left="lucide-radio"
                @click="() => { stopLive(); loadContent() }">
                Stop
              </Button>
              <a :href="logsApi.downloadUrl(selectedFile)" class="contents">
                <Button variant="subtle" class="w-full sm:w-auto" tooltip="Download">
                  <span class="size-4 lucide-download" />
                </Button>
              </a>
            </div>
          </div>

          <!-- Filename bar: mobile shows the filename next to the back button instead -->
          <div
            class="hidden md:block bg-surface-gray-1 px-4 py-2 border-b border-outline-gray-2 font-mono text-ink-gray-6 text-xs shrink-0">
            {{ selectedFile }}
          </div>

          <!-- Terminal area -->
          <div ref="viewer" class="flex flex-col flex-1 overflow-hidden">
            <div v-if="contentError" class="p-4 font-mono text-ink-red-4 text-sm">Error: {{ contentError }}</div>
            <LogView v-else ref="terminal" :lines="visibleLines" :streaming="liveMode" fill wrap divided
              :rounded="false" :empty-text="contentLoading ? 'Loading…' : 'Log file is empty.'" />
            <div v-if="rawLines.length"
              class="px-3 sm:px-4 py-1.5 sm:py-2 border-t border-outline-gray-2 text-ink-gray-4 text-xs shrink-0">
              Showing the last {{ linesCount }} of {{ totalLineCount }}
              <template v-if="search.trim()"> · {{ matchTotal }} match{{ matchTotal !== 1 ? 'es' : '' }}</template>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, ErrorMessage, FormControl, LoadingText } from 'frappe-ui'
import UpdatesAvailableButton from '@/components/UpdatesAvailableButton.vue'
import LogView from '@/components/LogView.vue'
import { logsApi } from '@/api/logs'
import { escapeHtml, processLine } from '@/utils/ansi'
import { formatBytes } from '@/utils/format'
import { useBench } from '@/composables/useBench'
import { useIsMobile } from '@/composables/useIsMobile'

const route = useRoute()
const router = useRouter()

const { name: benchName, load: loadBench } = useBench()

function shortRelativeTime(iso) {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function truncateFilename(name, max = 32) {
  return name.length > max ? `${name.slice(0, max - 1)}…` : name
}

function hasErrors(log) {
  return log.filename.endsWith('.error.log') && log.size_bytes > 0
}

// ── Log list ─────────────────────────────────────────────────────────────
const logs = ref([])
const logsLoading = ref(true)
const logsError = ref('')
const fileSearch = ref('')

const filteredLogs = computed(() => {
  const term = fileSearch.value.trim().toLowerCase()
  return term ? logs.value.filter((log) => log.filename.toLowerCase().includes(term)) : logs.value
})

const totalLineCount = computed(() =>
  logs.value.find((log) => log.filename === selectedFile.value)?.line_count ?? rawLines.value.length,
)

async function loadLogs() {
  logsLoading.value = true
  logsError.value = ''
  try {
    // Sort once here (most recently active first) — filteredLogs only needs to filter.
    logs.value = (await logsApi.list()).sort((a, b) => new Date(b.last_modified) - new Date(a.last_modified))
  } catch (caught) {
    logsError.value = caught.message || 'Failed to load logs'
  } finally {
    logsLoading.value = false
  }
}

// ── Viewer ───────────────────────────────────────────────────────────────
const selectedFile = ref(route.query.file || '')
const rawLines = ref([])
const contentLoading = ref(false)
const contentError = ref('')
const search = ref('')
const linesCount = ref(200)
const liveMode = ref(false)
const terminal = ref(null)
const viewer = ref(null)
const activeMatch = ref(0)
const matchTotal = ref(0)
let eventSource = null
let lastTerm = ''

// Below sm, the prev/next match controls are hidden (no room, no keyboard) —
// this intentionally shares the `sm` (640px) breakpoint with that template
// class, so it stays in sync with when those controls are actually visible.
const isMobile = useIsMobile()
// Above md (768px) both panes show side by side, matching the `md:` classes
// that switch the list/viewer layout — a separate breakpoint from isMobile above.
const isSinglePane = useIsMobile(768)

const isSearching = computed(() => search.value.trim().length > 0)

// ANSI processing only depends on the fetched content; re-run it once per
// fetch/live-tail update, not on every search keystroke.
const processedLines = computed(() => rawLines.value.map(processLine))

const searchPattern = computed(() => {
  const term = search.value.trim()
  return term ? new RegExp(escapeRegExp(escapeHtml(term)), 'gi') : null
})

// Keep every line for context; search only highlights matches in place. Each
// match is tagged with data-mi so we can jump between them.
const visibleLines = computed(() => {
  const pattern = searchPattern.value
  return pattern ? processedLines.value.map((line) => highlight(line, pattern)) : processedLines.value
})

watch(visibleLines, () => nextTick(syncMatches))
watch(linesCount, () => loadContent())

function syncMatches() {
  // Skip the DOM scan entirely when there's nothing to highlight — matters
  // most during live tail, where visibleLines otherwise changes every line.
  if (!isSearching.value) {
    matchTotal.value = 0
    activeMatch.value = -1
    return
  }
  const marks = matchEls()
  matchTotal.value = marks.length
  const term = search.value.trim()
  if (term !== lastTerm) {
    lastTerm = term
    activeMatch.value = marks.length ? 0 : -1
    paintMatches(!liveMode.value)
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

// On mobile there's no way to act on an "active" match (see isMobile above) —
// highlight everything the same instead of singling one out and auto-scrolling.
function paintMatches(scroll) {
  if (isMobile.value) {
    matchEls().forEach((el) => {
      el.style.background = '#f9e2af'
      el.style.boxShadow = 'none'
    })
    return
  }
  matchEls().forEach((el, index) => {
    const active = index === activeMatch.value
    el.style.background = active ? '#fab387' : '#f9e2af'
    el.style.boxShadow = active ? '0 0 0 2px #fab387' : 'none'
    if (active && scroll) el.scrollIntoView({ block: 'center' })
  })
}

watch(selectedFile, (filename) => {
  router.replace({ path: '/dev-tools/logs', query: filename ? { file: filename } : {} })
  stopLive()
  rawLines.value = []
  search.value = ''
  if (filename) loadContent()
})

async function loadContent() {
  if (!selectedFile.value) return
  contentLoading.value = true
  contentError.value = ''
  try {
    const data = await logsApi.read(selectedFile.value, linesCount.value)
    rawLines.value = data.lines
    if (!isSearching.value) {
      await nextTick()
      terminal.value?.scrollToBottom()
    }
  } catch (caught) {
    contentError.value = caught.message || 'Failed to load log'
  } finally {
    contentLoading.value = false
  }
}

function startLive() {
  liveMode.value = true
  rawLines.value = []
  eventSource = new EventSource(logsApi.streamUrl(selectedFile.value))
  eventSource.onmessage = (event) => {
    rawLines.value.push(event.data)
    if (rawLines.value.length > 2000) rawLines.value.shift()
    terminal.value?.scrollToBottom()
  }
  eventSource.onerror = () => stopLive()
}

function stopLive() {
  liveMode.value = false
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

// Wrap matches of a precompiled `pattern` in already-rendered HTML, touching
// only text between tags so the ANSI colour <span>s stay intact. Line text is
// HTML-escaped, so the pattern is built from an HTML-escaped term (see
// searchPattern) before matching.
function highlight(html, pattern) {
  return html.replace(/(<[^>]+>)|([^<]+)/g, (_, tag, text) =>
    tag || text.replace(pattern, (match) =>
      `<mark data-mi style="background:#f9e2af;color:#1e1e2e;border-radius:2px;padding:0 1px;">${match}</mark>`,
    ),
  )
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

onMounted(async () => {
  loadBench()
  await loadLogs()
  if (selectedFile.value) {
    loadContent()
  } else if (filteredLogs.value.length && !isSinglePane.value) {
    // Desktop shows both panes, so preselect the most recently active log. On
    // mobile (< md) only one pane is visible at a time — leave the list showing instead.
    selectedFile.value = filteredLogs.value[0].filename
  }
})

onUnmounted(() => stopLive())
</script>
