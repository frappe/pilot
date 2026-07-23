<template>
  <div
    v-if="state.visible"
    class="fixed inset-0 z-50 flex items-start justify-center sm:pt-[8vh]"
    @click="close"
  >
    <div class="fixed inset-0 bg-black/30"></div>
    <div
      class="relative z-10 flex h-full w-full flex-col overflow-hidden bg-surface-white sm:h-[78vh] sm:w-[1100px] sm:max-w-[94vw] sm:rounded-xl sm:border sm:border-outline-gray-2 shadow-2xl"
      @click.stop
    >
      <!-- search bar -->
      <div class="flex items-center gap-2 border-b border-outline-gray-1 px-3 py-3 sm:py-2">
        <span class="lucide-search h-4 w-4 text-ink-gray-4"></span>
        <input
          ref="input"
          v-model="query"
          placeholder="Search across files…"
          class="min-w-0 flex-1 border-0 bg-transparent text-base text-ink-gray-8 outline-none focus:ring-0 sm:text-sm"
          @keydown.down.prevent="move(1)"
          @keydown.up.prevent="move(-1)"
          @keydown.enter.prevent="choose()"
          @keydown.esc.prevent="close"
        />
        <button
          v-for="o in toggles"
          :key="o.id"
          class="rounded p-1 text-2xs font-semibold"
          :class="{
            'bg-surface-gray-4 text-ink-gray-9': opts[o.id],
            'text-ink-gray-5 hover:text-ink-gray-8': !opts[o.id],
          }"
          :title="o.title"
          @click="toggle(o.id)"
        >
          <span class="px-1">{{ o.label }}</span>
        </button>
        <span class="ml-1 text-2xs text-ink-gray-4">{{ flat.length }}</span>
        <button
          class="-mr-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
          aria-label="Close"
          @click="close"
        >
          <span class="lucide-x h-5 w-5 text-current sm:h-4 sm:w-4"></span>
        </button>
      </div>

      <div class="flex min-h-0 flex-1 flex-col sm:flex-row">
        <!-- results (left, full width on mobile) -->
        <div
          ref="listBox"
          class="flex w-full flex-col overflow-auto border-b border-outline-gray-1 sm:w-[440px] sm:shrink-0 sm:border-r sm:border-b-0"
          @scroll="onListScroll"
        >
          <template v-for="grp in limitedResults" :key="grp.file">
            <div class="flex items-center gap-1.5 px-3 pb-0.5 pt-2 text-2xs sm:pt-1.5">
              <FileIcon :name="baseName(grp.file)" class="h-4 w-4 shrink-0 sm:h-3.5 sm:w-3.5" />
              <span class="truncate font-medium text-ink-gray-7">{{ baseName(grp.file) }}</span>
              <span class="truncate text-ink-gray-4">{{ dirName(grp.file) }}</span>
            </div>
            <div
              v-for="(m, mi) in grp.matches"
              :key="mi"
              :data-i="grp.base + mi"
              class="flex cursor-pointer items-baseline gap-2 px-3 py-2 text-sm sm:py-0.5 sm:text-xs"
              :class="{
                'bg-surface-gray-3': grp.base + mi === sel,
                'hover:bg-surface-gray-2': grp.base + mi !== sel,
              }"
              @click="onResultClick(grp.base + mi)"
              @dblclick="choose()"
            >
              <span class="w-8 shrink-0 text-right tabular-nums text-ink-gray-4">{{ m.line }}</span>
              <span class="truncate font-mono text-ink-gray-7">{{ m.text.trim() }}</span>
            </div>
          </template>
          <div v-if="!flat.length" class="px-3 py-4 text-center text-xs text-ink-gray-4">
            {{ loading ? 'searching…' : query ? 'No matches' : 'Type to search' }}
          </div>
          <div v-else-if="limit < flat.length" class="px-3 py-2 text-center text-2xs text-ink-gray-4">
            scroll for more · showing {{ limit }} of {{ flat.length }}
          </div>
        </div>

        <!-- preview (right, hidden on mobile) -->
        <div class="hidden min-w-0 flex-1 overflow-auto bg-surface-gray-1 sm:block">
          <div v-if="!current" class="p-4 text-xs text-ink-gray-4">
            {{ loading ? 'searching…' : 'No results to preview.' }}
          </div>
          <template v-else>
            <div class="sticky top-0 flex items-center gap-1.5 border-b border-outline-gray-1 bg-surface-white px-3 py-1.5 text-xs">
              <FileIcon :name="baseName(current.file)" class="h-4 w-4 shrink-0" />
              <span class="truncate font-medium text-ink-gray-8">{{ baseName(current.file) }}</span>
              <span class="truncate text-2xs text-ink-gray-4">{{ current.file }}:{{ current.line }}</span>
            </div>
            <div v-if="previewLoading" class="p-4 text-xs text-ink-gray-4">loading…</div>
            <div v-else ref="previewBox" class="font-mono text-xs leading-[1.55]">
              <div
                v-for="ln in preview"
                :key="ln.n"
                :data-pln="ln.n"
                class="flex px-2"
                :class="{ 'bg-surface-amber-1': ln.n === current.line }"
              >
                <span class="w-12 shrink-0 select-none pr-3 text-right text-ink-gray-4 tabular-nums">{{ ln.n }}</span>
                <span class="whitespace-pre text-ink-gray-8">{{ ln.text }}</span>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick } from 'vue'
import FileIcon from '@/components/FileIcon.vue'
import { filesApi } from '@/api/files'
import { searchApi } from '@/api/search'
import { useEditorStore } from '@/stores/editor'
import { useSearchModal } from '@/composables/useSearchModal'
import { useMobile } from '@/composables/useMobile'
import { baseName, dirName } from '@/utils'

const editor = useEditorStore()
const { state, close } = useSearchModal()
const { isMobile } = useMobile()

const PAGE = 80

const query = ref('')
const loading = ref(false)
const results = ref([])
const sel = ref(0)
const input = ref(null)
const listBox = ref(null)
const previewBox = ref(null)
const opts = reactive({ case: false, word: false, regex: false })
const fileCache = new Map()
const previewLines = ref([])
const previewLoading = ref(false)

const toggles = [
  { id: 'case', label: 'Aa', title: 'Match case' },
  { id: 'word', label: 'ab', title: 'Whole word' },
  { id: 'regex', label: '.*', title: 'Regex' },
]

const flat = computed(() => {
  const arr = []
  results.value.forEach((g) => g.matches.forEach((m) => arr.push({ file: g.file, ...m })))
  return arr
})

const limit = ref(PAGE)
const limitedResults = computed(() => {
  const out = []
  let n = 0
  for (const g of results.value) {
    if (n >= limit.value) break
    const take = g.matches.slice(0, limit.value - n)
    out.push({ file: g.file, matches: take, base: n })
    n += take.length
  }
  return out
})
function onListScroll() {
  const el = listBox.value
  if (!el) return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 240 && limit.value < flat.value.length) {
    limit.value += PAGE
  }
}

const current = computed(() => flat.value[sel.value] || null)
const preview = computed(() => previewLines.value)

let timer
watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(run, 250)
})
function toggle(id) {
  opts[id] = !opts[id]
  run()
}

async function run() {
  clearTimeout(timer)
  const q = query.value
  if (!q) {
    results.value = []
    return
  }
  loading.value = true
  try {
    results.value = await searchApi.find({ q, caseSensitive: opts.case, word: opts.word, regex: opts.regex })
  } catch {
    results.value = []
  }
  sel.value = 0
  limit.value = PAGE
  if (listBox.value) listBox.value.scrollTop = 0
  loading.value = false
}

function move(d) {
  if (!flat.value.length) return
  sel.value = (sel.value + d + flat.value.length) % flat.value.length
  if (sel.value >= limit.value) limit.value = sel.value + PAGE
  nextTick(() => {
    listBox.value?.querySelector(`[data-i="${sel.value}"]`)?.scrollIntoView({ block: 'nearest' })
  })
}

function onResultClick(i) {
  sel.value = i
  if (isMobile.value) choose()
}

function choose() {
  const c = current.value
  if (!c) return
  close()
  editor.closeDiff()
  editor.open(c.file, { line: c.line, col: (c.start || 0) + 1, preview: true }).catch(() => {})
}

async function loadPreview() {
  const c = current.value
  if (!c) {
    previewLines.value = []
    return
  }
  let content = fileCache.get(c.file)
  if (content == null) {
    previewLoading.value = true
    try {
      const d = await filesApi.read(c.file)
      content = d.content || ''
    } catch {
      content = ''
    }
    fileCache.set(c.file, content)
    // Selection may have moved on while we were fetching.
    if (current.value !== c) {
      previewLoading.value = false
      return
    }
    previewLoading.value = false
  }
  const all = content.split('\n')
  const from = Math.max(1, c.line - 60)
  const to = Math.min(all.length, c.line + 60)
  const rows = []
  for (let n = from; n <= to; n++) rows.push({ n, text: all[n - 1] ?? '' })
  previewLines.value = rows
  nextTick(() => {
    previewBox.value
      ?.querySelector(`[data-pln="${c.line}"]`)
      ?.scrollIntoView({ block: 'center' })
  })
}

// Load the preview on demand, debounced so fast arrowing stays snappy.
let previewTimer
watch(current, () => {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(loadPreview, 90)
})

watch(
  () => state.visible,
  (v) => {
    if (v) {
      fileCache.clear()
      query.value = state.initial || ''
      sel.value = 0
      nextTick(() => {
        input.value?.focus()
        input.value?.select()
      })
      if (query.value) run()
      else results.value = []
    }
  },
)
</script>
