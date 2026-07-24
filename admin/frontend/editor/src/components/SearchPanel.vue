<template>
  <div class="flex h-full flex-col">
    <PanelHeader title="Search" @close="emit('close')" />

    <div class="flex items-start gap-1 px-3">
      <button
        class="ed-icon-button"
        :title="showReplace ? 'Hide replace' : 'Toggle replace'"
        @click="showReplace = !showReplace"
      >
        <span
          :class="showReplace ? 'lucide-chevron-down' : 'lucide-chevron-right'"
          class="h-4 w-4 text-current"
        ></span>
      </button>
      <div class="min-w-0 flex-1 space-y-1">
        <div
          class="flex h-9 items-center rounded-md border border-outline-gray-2 bg-surface-gray-2 pr-1 focus-within:border-outline-gray-3 sm:h-8"
        >
          <input
            ref="queryInput"
            v-model="query"
            placeholder="Search"
            class="min-w-0 flex-1 border-0 bg-transparent px-2 text-sm text-ink-gray-8 outline-none focus:ring-0"
            @keydown.down.prevent="move(1)"
            @keydown.up.prevent="move(-1)"
            @keydown.enter.prevent="onEnter"
          />
          <SearchToggles v-model="opts" @change="run" />
        </div>
        <div
          v-show="showReplace"
          class="flex h-9 items-center rounded-md border border-outline-gray-2 bg-surface-gray-2 pr-1 focus-within:border-outline-gray-3 sm:h-8"
        >
          <input
            v-model="replace"
            placeholder="Replace"
            class="min-w-0 flex-1 border-0 bg-transparent px-2 text-sm text-ink-gray-8 outline-none focus:ring-0"
            @keydown.enter="run"
          />
          <button
            class="ed-icon-button"
            :disabled="!results.length || !query"
            title="Replace all"
            @click="replaceAll"
          >
            <span class="lucide-repeat h-4 w-4 text-current"></span>
          </button>
        </div>
      </div>
    </div>

    <div class="flex h-7 shrink-0 items-center px-3 text-xs text-ink-gray-5">
      <span v-if="loading">Searching…</span>
      <span v-else-if="error" class="truncate text-ink-red-4" :title="error">{{ error }}</span>
      <span v-else-if="results.length">{{ summary }}</span>
    </div>

    <div ref="scroller" class="min-h-0 flex-1 overflow-auto px-1.5 pb-24 sm:pb-2" @scroll="onScroll">
      <div v-for="group in groups" :key="group.path">
        <div class="ed-row" @click="toggleCollapse(group.path)">
          <span class="ed-lane">
            <span
              :class="group.collapsed ? 'lucide-chevron-right' : 'lucide-chevron-down'"
              class="h-4 w-4 text-ink-gray-4"
            ></span>
          </span>
          <FileIcon :name="baseName(group.path)" class="ed-lane" />
          <span class="ed-name">{{ baseName(group.path) }}</span>
          <span class="ed-path">{{ dirName(group.path) }}</span>
          <span class="ed-meta ml-auto rounded-full bg-surface-gray-3 px-1.5 text-ink-gray-6">
            {{ group.count }}
          </span>
        </div>
        <div
          v-for="match in group.matches"
          :key="match.index"
          :data-i="match.index"
          class="ed-row gap-1.5 pl-3"
          :class="{ 'ed-row-selected': match.index === selected }"
          @click="open(match.index)"
        >
          <span class="ed-lineno">{{ match.line }}</span>
          <MatchLine :text="match.text" :path="group.path" :start="match.start" :end="match.end" />
        </div>
      </div>
      <div v-if="query && !loading && !results.length" class="ed-empty">
        {{ error || 'No matches' }}
      </div>
      <div v-else-if="rendered < results.length" class="ed-empty">
        Showing {{ rendered }} of {{ results.length }} files
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, nextTick } from 'vue'
import { confirmDialog } from 'frappe-ui'
import { searchApi } from '@/api/search'
import FileIcon from '@/components/FileIcon.vue'
import PanelHeader from '@/components/ui/PanelHeader.vue'
import SearchToggles from '@/components/ui/SearchToggles.vue'
import MatchLine from '@/components/ui/MatchLine.vue'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useSearch } from '@/composables/useSearch'
import { baseName, dirName } from '@/utils'

const emit = defineEmits(['close'])

const PAGE = 40

const editor = useEditorStore()
const git = useGitStore()
const { focusTick } = useSearch()

const query = ref('')
const replace = ref('')
const showReplace = ref(false)
const loading = ref(false)
const results = ref([])
const error = ref('')
const rendered = ref(PAGE)
const collapsed = reactive(new Set())
const queryInput = ref(null)
const scroller = ref(null)
const selected = ref(-1)
const opts = ref({ case: false, word: false, regex: false })

const totalMatches = computed(() => results.value.reduce((n, f) => n + f.matches.length, 0))
const summary = computed(() => {
  const files = results.value.length
  const hits = totalMatches.value
  return `${hits} ${hits === 1 ? 'result' : 'results'} in ${files} ${files === 1 ? 'file' : 'files'}`
})

// Colorizing every hit at once is wasteful on a broad query; grow on scroll.
const visibleResults = computed(() => results.value.slice(0, rendered.value))
function onScroll(event) {
  const el = event.target
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 240) {
    rendered.value = Math.min(rendered.value + PAGE, results.value.length)
  }
}

// Every rendered match carries its position in one flat list, so the arrow keys
// can walk across files without the template needing to count.
const groups = computed(() => {
  let index = 0
  return visibleResults.value.map((file) => {
    const isCollapsed = collapsed.has(file.path)
    return {
      path: file.path,
      count: file.matches.length,
      collapsed: isCollapsed,
      matches: isCollapsed ? [] : file.matches.map((match) => ({ ...match, index: index++ })),
    }
  })
})
const flat = computed(() => groups.value.flatMap((g) => g.matches.map((m) => ({ path: g.path, match: m }))))

function move(step) {
  // Step past the rendered window and it grows, so long result sets stay walkable.
  if (selected.value + step >= flat.value.length && rendered.value < results.value.length) {
    rendered.value = Math.min(rendered.value + PAGE, results.value.length)
  }
  const total = flat.value.length
  if (!total) return
  selected.value = (((selected.value + step) % total) + total) % total
  nextTick(() => {
    scroller.value?.querySelector(`[data-i="${selected.value}"]`)?.scrollIntoView({ block: 'nearest' })
  })
}

function onEnter() {
  if (selected.value >= 0) open(selected.value)
  else run()
}

function open(index) {
  const hit = flat.value[index]
  if (!hit) return
  selected.value = index
  editor
    .open(hit.path, { line: hit.match.line, col: (hit.match.start || 0) + 1, preview: true })
    .catch(() => {})
}

function focusQuery() {
  nextTick(() => {
    queryInput.value?.focus()
    queryInput.value?.select()
  })
}
watch(focusTick, focusQuery)

let timer
watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(run, 250)
})

function toggleCollapse(path) {
  collapsed.has(path) ? collapsed.delete(path) : collapsed.add(path)
  // Collapsing renumbers everything below it; start the walk over.
  selected.value = -1
}

async function run() {
  clearTimeout(timer)
  const q = query.value
  if (!q) {
    results.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const found = await searchApi.find(q, opts.value)
    results.value = found.map((f) => ({ path: f.file, matches: f.matches }))
  } catch (e) {
    results.value = []
    error.value = e.message || 'Search failed.'
  }
  rendered.value = PAGE
  selected.value = -1
  loading.value = false
}

function replaceAll() {
  if (!query.value || !results.value.length) return
  confirmDialog({
    title: 'Replace all',
    message: `Replace ${totalMatches.value} occurrence(s) across ${results.value.length} file(s)?`,
    onConfirm: async ({ hideDialog }) => {
      hideDialog()
      const files = results.value.map((f) => f.path)
      await searchApi.replace({ query: query.value, replace: replace.value, ...opts.value, files })
      for (const path of files) {
        if (editor.tabs.find((t) => t.path === path)) await editor.refreshTab(path)
      }
      git.refresh()
      run()
    },
  })
}

onMounted(() => {
  const q = new URLSearchParams(location.search).get('q')
  if (q) query.value = q
  focusQuery()
})
</script>
