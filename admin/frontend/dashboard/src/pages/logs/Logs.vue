<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Combobox, ErrorMessage, LoadingText, Select, TextInput, Tooltip } from 'frappe-ui'

import EmptyState from '@/components/common/EmptyState.vue'
import LogView from '@/components/logs/LogView.vue'
import Scrollbar from '@/components/common/Scrollbar.vue'

import { logsApi } from '@/api/logs'
import { escapeHtml, processLine } from '@/utils/ansi'
import { formatBytes } from '@/utils/format'
import { relativeTime } from '@/utils/time'
import { useBench } from '@/composables/benches/useBench'
import { useIsMobile } from '@/composables/common/useIsMobile'

const route = useRoute()
const router = useRouter()

const { name: benchName, load: loadBench } = useBench()

const hasErrors = (log) => {
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

// only for mobile view
const fileOptions = computed(() =>
  logs.value.map((log) => ({
    label: log.filename,
    value: log.filename,
    size: formatBytes(log.size_bytes),
    time: relativeTime(log.last_modified),
    errors: hasErrors(log),
  })),
)

const totalLineCount = computed(
  () =>
    logs.value.find((log) => log.filename === selectedFile.value)?.line_count ??
    rawLines.value.length,
)

const loadLogs = async () => {
  logsLoading.value = true
  logsError.value = ''
  try {
    // Sort once here (most recently active first) - filteredLogs only needs to filter.
    logs.value = (await logsApi.list()).sort(
      (a, b) => new Date(b.last_modified) - new Date(a.last_modified),
    )
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
const activeMatch = ref(0)
const matchTotal = ref(0)
let eventSource = null
let lastTerm = ''

const isMobile = useIsMobile(768)

const isSearching = computed(() => search.value.trim().length > 0)

// Re-run ANSI processing per fetch, not per search keystroke.
const processedLines = computed(() => rawLines.value.map(processLine))

const searchPattern = computed(() => {
  const term = search.value.trim()
  return term ? new RegExp(escapeRegExp(escapeHtml(term)), 'gi') : null
})

// Search highlights in place; data-mi tags matches for jumping.
const visibleLines = computed(() => {
  const pattern = searchPattern.value
  return pattern
    ? processedLines.value.map((line) => highlight(line, pattern))
    : processedLines.value
})

watch(visibleLines, () => nextTick(syncMatches))
watch(linesCount, () => loadContent())

const syncMatches = () => {
  // Skip the DOM scan entirely when there's nothing to highlight - matters
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

const gotoMatch = (delta) => {
  const marks = matchEls()
  if (!marks.length) return
  activeMatch.value = (activeMatch.value + delta + marks.length) % marks.length
  paintMatches(true)
}

const matchEls = () => {
  const root = terminal.value?.$el
  return root ? [...root.querySelectorAll('mark[data-mi]')] : []
}

const paintMatches = (scroll) => {
  matchEls().forEach((el, index) => {
    const active = index === activeMatch.value
    el.classList.toggle('log-match--active', active)
    if (active && scroll) el.scrollIntoView({ block: 'center' })
  })
}

watch(selectedFile, (filename) => {
  router.replace({ path: '/insights/logs', query: filename ? { file: filename } : {} })
  stopLive()
  rawLines.value = []
  search.value = ''
  if (filename) loadContent()
})

const loadContent = async () => {
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

const startLive = () => {
  liveMode.value = true
  rawLines.value = []
  eventSource = new EventSource(logsApi.streamUrl(selectedFile.value))
  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data)
    rawLines.value.push(data.error ? `ERROR: ${data.error}` : data.line)
    if (rawLines.value.length > 2000) rawLines.value.shift()
  }
  eventSource.onerror = () => stopLive()
}

const stopLive = () => {
  liveMode.value = false
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

const toggleLive = () => {
  if (!liveMode.value) return startLive()
  stopLive()
  loadContent()
}

// Wrap matches in rendered HTML, touching only text between tags so ANSI
// <span>s stay intact; the pattern is built from an HTML-escaped term.
const highlight = (html, pattern) => {
  return html.replace(
    /(<[^>]+>)|([^<]+)/g,
    (_, tag, text) =>
      tag || text.replace(pattern, (match) => `<mark data-mi class="log-match">${match}</mark>`),
  )
}

const escapeRegExp = (text) => {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

onMounted(async () => {
  loadBench()
  await loadLogs()
  if (selectedFile.value) {
    loadContent()
  } else if (filteredLogs.value.length) {
    selectedFile.value = filteredLogs.value[0].filename
  }
})

onUnmounted(() => stopLive())
</script>

<template>
  <div class="p-3 md:p-4 flex h-[calc(100dvh-6.5rem)] md:h-[calc(100dvh-3rem)] overflow-hidden">
    <!-- search logs  -->
    <aside class="hidden md:flex flex-col w-64 shrink-0">
      <TextInput v-model="fileSearch" placeholder="Search log files" class="shrink-0 mr-4">
        <template #prefix><span class="size-4 text-ink-gray-5 lucide-search" /></template>
      </TextInput>

      <Scrollbar class="flex-1 min-h-0 pr-4">
        <LoadingText v-if="logsLoading" class="p-2" />
        <ErrorMessage v-else-if="logsError" :message="logsError" class="p-2" />
        <p v-else-if="!filteredLogs.length" class="p-2 text-ink-gray-4 text-sm">
          No log files found.
        </p>

        <button
          v-else
          v-for="log in filteredLogs"
          :key="log.filename"
          class="flex flex-wrap items-center gap-x-2 mt-1 first:mt-2 px-3 py-2.5 rounded-4 w-full text-left transition-colors"
          :class="selectedFile === log.filename ? 'bg-surface-gray-2' : 'hover:bg-surface-gray-1'"
          @click="selectedFile = log.filename"
        >
          <span class="flex-1 min-w-0 font-medium text-ink-gray-8 truncate">
            {{ log.filename }}
          </span>

          <Tooltip v-if="hasErrors(log)" text="Contains errors">
            <span
              class="bg-surface-red-5 rounded-full size-1.5 shrink-0"
              role="img"
              aria-label="Contains errors"
            />
          </Tooltip>

          <span class="text-ink-gray-4 text-sm shrink-0">
            {{ relativeTime(log.last_modified) }}
          </span>

          <span class="mt-1.5 w-full text-ink-gray-4 text-xs"
            >{{ formatBytes(log.size_bytes) }}</span
          >
        </button>
      </Scrollbar>
    </aside>

    <div class="flex flex-col flex-1 min-w-0 min-h-0 ">
      <!-- search logs for mobilevie -->
      <Combobox
        class="md:hidden mb-2"
        size="md"
        side="bottom"
        placeholder="Select a log file"
        :model-value="selectedFile"
        :options="fileOptions"
        @update:model-value="selectedFile = $event"
      >
        <template #prefix><span class="size-4 text-ink-gray-5 lucide-search" /></template>

        <template #item-label="{ item }">
          <div class="flex items-center gap-2">
            <span class="min-w-0 font-medium text-ink-gray-8 truncate">{{ item.label }}</span>

            <span
              v-if="item.errors"
              class="bg-surface-red-5 rounded-full size-1.5 shrink-0"
              role="img"
              aria-label="Contains errors"
            />

            <span class="ml-auto text-ink-gray-4 text-sm shrink-0">{{ item.time }}</span>
          </div>

          <div class="mt-0.5 text-ink-gray-4 text-sm">{{ item.size }}</div>
        </template>
      </Combobox>

      <EmptyState
        :bordered="false"
        icon="lucide-scroll-text"
        title="Select a log file"
        v-if="!selectedFile"
        :description="`Output from ${benchName}'s services.`"
        class="m-auto"
      />

      <!-- terminal output filters -->
      <template v-else>
        <div class="flex items-center gap-2 pb-2 shrink-0">
          <TextInput
            v-model="search"
            placeholder="Search this log"
            :size="isMobile ? 'md' : 'sm'"
            class="flex-1 min-w-0"
            @keydown.enter.exact.prevent="gotoMatch(1)"
            @keydown.enter.shift.prevent="gotoMatch(-1)"
          />

          <template v-if="isSearching">
            <span class="text-ink-gray-5 text-xs tabular-nums"
              >{{ matchTotal ? activeMatch + 1 : 0 }}/{{ matchTotal }}</span
            >
            <Button
              class="-ms-1"
              icon="lucide-chevron-up"
              :size="isMobile ? 'md' : 'sm'"
              label="Previous match"
              tooltip="Previous (Shift+Enter)"
              :disabled="!matchTotal"
              @click="gotoMatch(-1)"
            />
            <Button
              class="-ms-1"
              icon="lucide-chevron-down"
              :size="isMobile ? 'md' : 'sm'"
              label="Next match"
              tooltip="Next (Enter)"
              :disabled="!matchTotal"
              @click="gotoMatch(1)"
            />
          </template>

          <Select
            v-model="linesCount"
            :size="isMobile ? 'md' : 'sm'"
            :disabled="liveMode"
            :options="[
                { label: '100 lines', value: 100 },
                { label: '200 lines', value: 200 },
                { label: '500 lines', value: 500 },
                { label: '1000 lines', value: 1000 },
              ]"
          />

          <Button
            icon="lucide-refresh-cw"
            :size="isMobile ? 'md' : 'sm'"
            label="Refresh"
            tooltip="Refresh"
            :loading="contentLoading"
            :disabled="liveMode"
            @click="loadContent"
          />

          <Button
            :icon="liveMode ? 'lucide-square' : 'lucide-radio'"
            :theme="liveMode ? 'red' : 'gray'"
            :size="isMobile ? 'md' : 'sm'"
            :label="liveMode ? 'Stop ' : '' + 'live tail'"
            :tooltip="(liveMode ? 'Stop ' : '') + 'live tail'"
            @click="toggleLive"
          />

          <Button
            :link="logsApi.downloadUrl(selectedFile)"
            icon="lucide-download"
            :size="isMobile ? 'md' : 'sm'"
            label="Download"
            tooltip="Download"
          />
        </div>

        <div v-if="contentError" class="p-4 font-mono text-ink-red-5 text-sm">
          Error: {{ contentError }}
        </div>

        <LogView
          ref="terminal"
          :lines="visibleLines"
          :streaming="liveMode"
          fill
          wrap
          rounded
          :empty-text="contentLoading ? 'Loading…' : 'Log file is empty.'"
        />

        <div v-if="rawLines.length" class="md:px-4 pt-2 text-ink-gray-4 text-xs shrink-0">
          {{ totalLineCount }}
          lines in file
          <template v-if="search.trim()">
            · {{ matchTotal }} match{{ matchTotal !== 1 ? 'es' : '' }}</template
          >
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
:deep(.log-match) {
  background: var(--surface-amber-3);
  color: var(--ink-gray-9);
  border-radius: 2px;
}
:deep(.log-match--active) {
  background: var(--surface-amber-5);
  box-shadow: 0 0 0 2px var(--surface-amber-5);
}
</style>
