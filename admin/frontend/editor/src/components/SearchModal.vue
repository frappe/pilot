<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, ref, watch } from 'vue'

import { filesApi } from '@/api/files'
import { type SearchMatch, type SearchOptions, type SearchResult, searchApi } from '@/api/search'
import FileIcon from '@/components/FileIcon.vue'
import MatchLine from '@/components/ui/MatchLine.vue'
import Modal from '@/components/ui/Modal.vue'
import SearchToggles from '@/components/ui/SearchToggles.vue'
import { useMobile } from '@/composables/useMobile'
import { useSearchModal } from '@/composables/useSearchModal'
import { useEditorStore } from '@/stores/editor'
import { baseName, dirName } from '@/utils'

const CodePreview = defineAsyncComponent(() => import('@/components/ui/CodePreview.vue'))

const editor = useEditorStore()
const { state, close } = useSearchModal()
const { isMobile } = useMobile()

const page = 80

const query = ref('')
const loading = ref(false)
const results = ref<SearchResult[]>([])
const error = ref('')
const sel = ref(0)
const input = ref<HTMLInputElement>()
const listBox = ref<HTMLElement>()
const opts = ref<SearchOptions>({ case: false, word: false, regex: false })
const fileCache = new Map<string, string>()
const previewContent = ref('')
const previewLoading = ref(false)

const flat = computed(() =>
  results.value.flatMap((g) => g.matches.map((m) => ({ file: g.file, ...m }))),
)

const current = computed(() => flat.value[sel.value] || null)

const countLabel = computed(() =>
  flat.value.length ? `${sel.value + 1}/${flat.value.length}` : '',
)

const limit = ref(page)

const limitedResults = computed(() => {
  const out: { file: string; matches: SearchMatch[]; base: number; count: number }[] = []

  let n = 0

  for (const g of results.value) {
    if (n >= limit.value) break

    const take = g.matches.slice(0, limit.value - n)

    out.push({ file: g.file, matches: take, base: n, count: g.matches.length })
    n += take.length
  }

  return out
})

const onListScroll = () => {
  const el = listBox.value

  if (!el) return

  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 240 && limit.value < flat.value.length) {
    limit.value += page
  }
}

let timer: ReturnType<typeof setTimeout> | undefined

const run = async () => {
  clearTimeout(timer)

  const q = query.value

  if (!q) {
    results.value = []
    return
  }

  loading.value = true
  error.value = ''

  try {
    results.value = await searchApi.find(q, opts.value)
  } catch (e) {
    results.value = []
    error.value = (e as Error).message || 'Search failed.'
  }

  sel.value = 0
  limit.value = page

  if (listBox.value) listBox.value.scrollTop = 0

  loading.value = false
}

watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(run, 250)
})

const move = (d: number) => {
  if (!flat.value.length) return

  sel.value = (sel.value + d + flat.value.length) % flat.value.length

  if (sel.value >= limit.value) limit.value = sel.value + page

  nextTick(() => {
    listBox.value?.querySelector(`[data-i="${sel.value}"]`)?.scrollIntoView({ block: 'nearest' })
  })
}

const choose = () => {
  const c = current.value

  if (!c) return

  close()
  editor.closeDiff()
  editor.open(c.file, { line: c.line, col: (c.start || 0) + 1, preview: true }).catch(() => {})
}

const onResultClick = (i: number) => {
  sel.value = i

  if (isMobile.value) choose()
}

const loadPreview = async () => {
  const c = current.value

  if (!c) {
    previewContent.value = ''
    return
  }

  let content = fileCache.get(c.file)

  if (content == null) {
    previewLoading.value = true

    try {
      content = (await filesApi.read(c.file)).content || ''
    } catch {
      content = ''
    }

    fileCache.set(c.file, content)
    previewLoading.value = false
    // Selection may have moved on while we were fetching.
    if (current.value !== c) return
  }

  previewContent.value = content
}

// Load the preview on demand, debounced so fast arrowing stays snappy.
let previewTimer: ReturnType<typeof setTimeout> | undefined

watch(current, () => {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(loadPreview, 90)
})

watch(
  () => state.visible,
  (visible) => {
    if (!visible) return

    fileCache.clear()
    query.value = state.initial || ''
    sel.value = 0
    nextTick(() => {
      input.value?.focus()
      input.value?.select()
    })

    if (query.value) run()
    else results.value = []
  },
)
</script>

<template>
  <Modal v-if="state.visible" size-class="sm:h-[78vh] sm:w-[1100px] sm:max-w-[94vw]" @close="close">
    <template #input>
      <input
        ref="input"
        v-model="query"
        placeholder="Search across files…"
        class="min-w-0 flex-1 border-0 bg-transparent text-sm text-ink-gray-8 outline-none focus:ring-0"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter.prevent="choose()"
        @keydown.esc.prevent="close"
      />
    </template>
    <template #actions>
      <SearchToggles v-model="opts" @change="run" />
      <span v-if="countLabel" class="ed-meta shrink-0 tabular-nums">{{ countLabel }}</span>
    </template>

    <div class="flex min-h-0 flex-1 flex-col sm:flex-row">
      <div
        ref="listBox"
        class="flex w-full flex-col overflow-auto border-b border-outline-gray-1 px-1.5 sm:w-[420px] sm:shrink-0 sm:border-b-0 sm:border-r"
        @scroll="onListScroll"
      >
        <div v-for="group in limitedResults" :key="group.file">
          <div class="ed-row cursor-default hover:bg-transparent">
            <span class="ed-lane"></span>
            <FileIcon :name="baseName(group.file)" class="ed-lane" />
            <span class="ed-name">{{ baseName(group.file) }}</span>
            <span class="ed-path">{{ dirName(group.file) }}</span>
            <span class="ed-meta ml-auto rounded-full bg-surface-gray-3 px-1.5 text-ink-gray-6">
              {{ group.count }}
            </span>
          </div>
          <div
            v-for="(match, index) in group.matches"
            :key="index"
            :data-i="group.base + index"
            class="ed-row gap-1.5 pl-3"
            :class="{ 'ed-row-selected': group.base + index === sel }"
            @click="onResultClick(group.base + index)"
            @dblclick="choose()"
          >
            <span class="ed-lineno">{{ match.line }}</span>
            <MatchLine
              :text="match.text"
              :path="group.file"
              :start="match.start"
              :end="match.end"
            />
          </div>
        </div>
        <div v-if="!flat.length" class="ed-empty m-auto" :class="{ 'text-ink-red-7': error }">
          {{ loading ? 'Searching…' : error || (query ? 'No matches' : 'Type to search') }}
        </div>
        <div v-else-if="limit < flat.length" class="ed-empty">
          Showing {{ limit }} of {{ flat.length }} matches
        </div>
      </div>

      <div class="hidden min-w-0 flex-1 flex-col sm:flex">
        <div v-if="!current" class="ed-empty m-auto">
          {{ loading ? 'Searching…' : 'No results to preview.' }}
        </div>
        <template v-else>
          <div class="flex h-9 shrink-0 items-center gap-2 border-b border-outline-gray-1 px-3">
            <FileIcon :name="baseName(current.file)" class="ed-lane" />
            <span class="ed-name text-ink-gray-8">{{ baseName(current.file) }}</span>
            <span class="ed-path">{{ dirName(current.file) }}</span>
            <span class="ed-meta">Line {{ current.line }}</span>
            <button class="ed-icon-button ml-auto" title="Open file" @click="choose()">
              <span class="lucide-external-link h-4 w-4 text-current"></span>
            </button>
          </div>
          <div class="min-h-0 flex-1">
            <div v-if="previewLoading" class="ed-empty">Loading…</div>
            <CodePreview
              v-else
              :content="previewContent"
              :path="current.file"
              :line="current.line"
            />
          </div>
        </template>
      </div>
    </div>
  </Modal>
</template>
