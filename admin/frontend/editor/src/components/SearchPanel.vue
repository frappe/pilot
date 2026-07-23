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
            @keydown.enter="run"
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
      <span v-else-if="query">{{ summary }}</span>
    </div>

    <div class="min-h-0 flex-1 overflow-auto px-1.5 pb-24 sm:pb-2" @scroll="onScroll">
      <div v-for="file in visibleResults" :key="file.path">
        <div class="ed-row" @click="toggleCollapse(file.path)">
          <span class="ed-lane">
            <span
              :class="collapsed.has(file.path) ? 'lucide-chevron-right' : 'lucide-chevron-down'"
              class="h-4 w-4 text-ink-gray-4"
            ></span>
          </span>
          <FileIcon :name="baseName(file.path)" class="ed-lane" />
          <span class="ed-name">{{ baseName(file.path) }}</span>
          <span class="ed-path">{{ dirName(file.path) }}</span>
          <span class="ed-meta ml-auto rounded-full bg-surface-gray-3 px-1.5 text-ink-gray-6">
            {{ file.matches.length }}
          </span>
        </div>
        <div v-show="!collapsed.has(file.path)">
          <div
            v-for="(match, index) in file.matches"
            :key="index"
            class="ed-row pl-8"
            @click="goto(file.path, match)"
          >
            <span class="ed-lineno">{{ match.line }}</span>
            <MatchLine :text="match.text" :path="file.path" :start="match.start" :end="match.end" />
          </div>
        </div>
      </div>
      <div v-if="query && !loading && !results.length" class="ed-empty">No matches</div>
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
const rendered = ref(PAGE)
const collapsed = reactive(new Set())
const queryInput = ref(null)
const opts = ref({ case: false, word: false, regex: false })

const totalMatches = computed(() => results.value.reduce((n, f) => n + f.matches.length, 0))
const summary = computed(() => {
  const files = results.value.length
  if (!files) return 'No matches'
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
    const found = await searchApi.find(q, opts.value)
    results.value = found.map((f) => ({ path: f.file, matches: f.matches }))
  } catch {
    results.value = []
  }
  rendered.value = PAGE
  loading.value = false
}

function goto(path, match) {
  editor.open(path, { line: match.line, col: (match.start || 0) + 1, preview: true }).catch(() => {})
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
