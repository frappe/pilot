<template>
  <div
    v-if="state.visible"
    class="fixed inset-0 z-50 flex items-stretch justify-center sm:items-start sm:pt-[10vh]"
    @click="close"
  >
    <div class="fixed inset-0 bg-black/20"></div>
    <div
      class="relative z-10 flex h-full w-full flex-col overflow-hidden bg-surface-white shadow-2xl sm:h-auto sm:max-h-[70vh] sm:w-[560px] sm:max-w-[90vw] sm:rounded-xl sm:border sm:border-outline-gray-2"
      @click.stop
    >
      <div class="flex items-center gap-2 border-b border-outline-gray-1 px-3 py-3 sm:py-2">
        <span class="lucide-search h-4 w-4 text-ink-gray-4"></span>
        <input
          ref="input"
          v-model="query"
          placeholder="Search files by name…"
          class="min-w-0 flex-1 border-0 bg-transparent text-base text-ink-gray-8 outline-none focus:ring-0 sm:text-sm"
          @keydown.down.prevent="move(1)"
          @keydown.up.prevent="move(-1)"
          @keydown.enter.prevent="choose()"
          @keydown.esc.prevent="close"
        />
        <span class="text-2xs text-ink-gray-4">{{ results.length }}</span>
        <button
          class="-mr-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
          aria-label="Close"
          @click="close"
        >
          <span class="lucide-x h-5 w-5 text-current sm:h-4 sm:w-4"></span>
        </button>
      </div>
      <div ref="list" class="min-h-0 flex-1 overflow-auto py-1">
        <div
          v-for="(p, i) in results"
          :key="p"
          :data-i="i"
          class="flex cursor-pointer items-center gap-2 px-3 py-3 text-base sm:py-1.5 sm:text-sm"
          :class="{ 'bg-surface-gray-3': i === sel, 'hover:bg-surface-gray-2': i !== sel }"
          @click="choose(i)"
          @mousemove="sel = i"
        >
          <FileIcon :name="baseName(p)" class="h-5 w-5 sm:h-4 sm:w-4" />
          <span class="truncate text-ink-gray-8">{{ baseName(p) }}</span>
          <span class="truncate text-sm text-ink-gray-4 sm:text-2xs">{{ dirName(p) }}</span>
        </div>
        <div v-if="!results.length" class="px-3 py-4 text-center text-xs text-ink-gray-4">
          {{ loading ? 'loading…' : 'No matching files' }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import FileIcon from '@/components/FileIcon.vue'
import { api } from '@/api'
import { useEditorStore } from '@/stores/editor'
import { useQuickOpen } from '@/composables/useQuickOpen'
import { baseName, dirName } from '@/utils'

const editor = useEditorStore()
const { state, close } = useQuickOpen()

const query = ref('')
const sel = ref(0)
const input = ref(null)
const list = ref(null)
const loading = ref(false)
const files = ref([])
let loadedOnce = false

function score(q, lp, orig) {
  if (!q) return 0
  let qi = 0
  let s = 0
  let prev = -2
  for (let i = 0; i < lp.length && qi < q.length; i++) {
    if (lp[i] === q[qi]) {
      s += i === prev + 1 ? 2 : 1
      prev = i
      qi++
    }
  }
  if (qi < q.length) return -1
  if (baseName(orig).toLowerCase().includes(q)) s += 10
  return s
}

const results = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return files.value.slice(0, 300)
  const scored = []
  for (const p of files.value) {
    const sc = score(q, p.toLowerCase(), p)
    if (sc >= 0) scored.push([p, sc])
  }
  scored.sort((a, b) => b[1] - a[1] || a[0].length - b[0].length)
  return scored.slice(0, 300).map((r) => r[0])
})

watch(results, () => {
  sel.value = 0
})

function move(d) {
  if (!results.value.length) return
  sel.value = (sel.value + d + results.value.length) % results.value.length
  nextTick(() => {
    const el = list.value?.querySelector(`[data-i="${sel.value}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  })
}

function choose(i) {
  const p = results.value[i ?? sel.value]
  if (!p) return
  close()
  editor.closeDiff()
  editor.open(p).catch(() => {})
}

async function loadFiles() {
  if (loadedOnce) return
  loading.value = true
  files.value = await api.files()
  loadedOnce = true
  loading.value = false
}

watch(
  () => state.visible,
  (v) => {
    if (v) {
      query.value = ''
      sel.value = 0
      loadFiles()
      nextTick(() => input.value?.focus())
    }
  },
)
</script>
