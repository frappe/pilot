<template>
  <div class="flex h-full flex-col">
    <div class="flex h-11 shrink-0 items-center justify-between border-b border-outline-gray-1 px-3 sm:h-auto sm:border-b-0 sm:py-2">
      <span class="text-sm font-semibold text-ink-gray-8 sm:text-2xs sm:uppercase sm:tracking-wider sm:text-ink-gray-5">
        Search
      </span>
      <button class="rounded p-1.5 text-ink-gray-6 hover:bg-surface-gray-2 sm:hidden" aria-label="Close" @click="emit('close')">
        <span class="lucide-x h-[18px] w-[18px] text-current"></span>
      </button>
    </div>

    <div class="flex gap-1 px-3">
      <button
        class="mt-2 self-start text-ink-gray-5 hover:text-ink-gray-8 sm:mt-1.5"
        :title="showReplace ? 'Hide replace' : 'Toggle replace'"
        @click="showReplace = !showReplace"
      >
        <span :class="showReplace ? 'lucide-chevron-down' : 'lucide-chevron-right'" class="h-5 w-5 text-current sm:h-4 sm:w-4"></span>
      </button>
      <div class="min-w-0 flex-1 space-y-1">
        <div class="flex items-center rounded-md border border-outline-gray-2 bg-surface-gray-2 pr-1 focus-within:border-outline-gray-3">
          <input
            ref="queryInput"
            v-model="query"
            placeholder="Search"
            class="min-w-0 flex-1 border-0 bg-transparent px-2 py-2 text-base text-ink-gray-8 outline-none focus:ring-0 sm:py-1 sm:text-sm"
            @keydown.enter="run"
          />
          <button
            v-for="o in toggles"
            :key="o.id"
            class="rounded p-1.5 text-xs font-semibold sm:p-0.5 sm:text-2xs"
            :class="{
              'bg-surface-gray-4 text-ink-gray-9': opts[o.id],
              'text-ink-gray-5 hover:text-ink-gray-8': !opts[o.id],
            }"
            :title="o.title"
            @click="toggle(o.id)"
          >
            <span class="px-0.5">{{ o.label }}</span>
          </button>
        </div>
        <div v-show="showReplace" class="flex items-center rounded-md border border-outline-gray-2 bg-surface-gray-2 pr-1 focus-within:border-outline-gray-3">
          <input
            v-model="replace"
            placeholder="Replace"
            class="min-w-0 flex-1 border-0 bg-transparent px-2 py-2 text-base text-ink-gray-8 outline-none focus:ring-0 sm:py-1 sm:text-sm"
            @keydown.enter="run"
          />
          <button
            class="rounded p-2 text-ink-gray-5 hover:text-ink-gray-8 disabled:opacity-40 sm:p-1"
            :disabled="!results.length || !query"
            title="Replace all"
            @click="replaceAll"
          >
            <span class="lucide-repeat h-5 w-5 text-current sm:h-3.5 sm:w-3.5"></span>
          </button>
        </div>
      </div>
    </div>

    <div class="mt-2 flex items-center justify-between px-3 text-xs text-ink-gray-5 sm:text-2xs">
      <span v-if="loading">searching…</span>
      <span v-else-if="query">{{ totalMatches }} in {{ results.length }} files</span>
      <span v-else></span>
    </div>

    <div class="mt-1 min-h-0 flex-1 overflow-auto px-1.5 pb-24 sm:pb-2">
      <div v-for="f in results" :key="f.file" class="mb-0.5">
        <div
          class="flex cursor-pointer items-center gap-1.5 rounded-md py-2 pl-1 pr-2 text-sm text-ink-gray-8 hover:bg-surface-gray-2 sm:py-1"
          @click="toggleCollapse(f.file)"
        >
          <span :class="collapsed.has(f.file) ? 'lucide-chevron-right' : 'lucide-chevron-down'" class="h-4 w-4 shrink-0 text-ink-gray-4 sm:h-3.5 sm:w-3.5"></span>
          <FileIcon :name="baseName(f.file)" class="h-4 w-4" />
          <span class="truncate">{{ baseName(f.file) }}</span>
          <span class="truncate text-xs text-ink-gray-4 sm:text-2xs">{{ dirName(f.file) }}</span>
          <span class="ml-auto rounded-full bg-surface-gray-3 px-1.5 text-xs text-ink-gray-6 sm:text-2xs">{{ f.matches.length }}</span>
        </div>
        <div v-show="!collapsed.has(f.file)">
          <div
            v-for="(m, i) in f.matches"
            :key="i"
            class="flex cursor-pointer items-baseline gap-2 rounded-md py-1.5 pl-7 pr-2 hover:bg-surface-gray-2 sm:py-0.5"
            @click="goto(m, f.file)"
          >
            <span class="w-8 shrink-0 text-right text-2xs tabular-nums text-ink-gray-4">{{ m.line }}</span>
            <span class="truncate font-mono text-xs text-ink-gray-6">
              {{ pre(m) }}<mark class="rounded-sm bg-surface-amber-2 text-ink-gray-9">{{ hit(m) }}</mark>{{ post(m) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, nextTick } from 'vue'
import FileIcon from '@/components/FileIcon.vue'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useSearch } from '@/composables/useSearch'
import { baseName, dirName } from '@/utils'

const emit = defineEmits(['close'])

const editor = useEditorStore()
const git = useGitStore()
const { focusTick } = useSearch()

function focusQuery() {
  nextTick(() => {
    queryInput.value?.focus()
    queryInput.value?.select()
  })
}
watch(focusTick, focusQuery)

const query = ref('')
const replace = ref('')
const showReplace = ref(false)
const loading = ref(false)
const results = ref([])
const collapsed = reactive(new Set())
const queryInput = ref(null)
const opts = reactive({ case: false, word: false, regex: false })

const toggles = [
  { id: 'case', label: 'Aa', title: 'Match case' },
  { id: 'word', label: 'ab', title: 'Whole word' },
  { id: 'regex', label: '.*', title: 'Regex' },
]

const totalMatches = computed(() => results.value.reduce((n, f) => n + f.matches.length, 0))

function pre(m) {
  return m.text.slice(0, m.start).replace(/^\s+/, '')
}
function hit(m) {
  return m.text.slice(m.start, m.end)
}
function post(m) {
  return m.text.slice(m.end).replace(/\n$/, '')
}

let timer
watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(run, 250)
})
function toggle(id) {
  opts[id] = !opts[id]
  run()
}
function toggleCollapse(file) {
  collapsed.has(file) ? collapsed.delete(file) : collapsed.add(file)
}

async function run() {
  clearTimeout(timer)
  const q = query.value
  if (!q) {
    results.value = []
    return
  }
  loading.value = true
  const params = new URLSearchParams({ q })
  if (opts.case) params.set('case', '1')
  if (opts.word) params.set('word', '1')
  if (opts.regex) params.set('regex', '1')
  try {
    const res = await fetch('/api/search?' + params)
    results.value = await res.json()
  } catch {
    results.value = []
  }
  loading.value = false
}

function goto(m, file) {
  editor.open(file, m.line, (m.start || 0) + 1).catch(() => {})
}

async function replaceAll() {
  if (!query.value || !results.value.length) return
  if (!confirm(`Replace ${totalMatches.value} occurrence(s) across ${results.value.length} file(s)?`)) return
  const files = results.value.map((f) => f.file)
  await fetch('/api/replace', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: query.value,
      replace: replace.value,
      regex: opts.regex,
      case: opts.case,
      word: opts.word,
      files,
    }),
  })
  for (const p of files) {
    if (editor.tabs.find((t) => t.path === p)) await editor.refreshTab(p)
  }
  git.refresh()
  run()
}

onMounted(() => {
  const q = new URLSearchParams(location.search).get('q')
  if (q) query.value = q
  focusQuery()
})
</script>
