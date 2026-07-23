<template>
  <Modal v-if="state.visible" width-class="sm:max-h-[70vh] sm:w-[560px] sm:max-w-[90vw]" @close="close">
    <template #input>
      <input
        ref="input"
        v-model="query"
        placeholder="Search files by name…"
        class="min-w-0 flex-1 border-0 bg-transparent text-sm text-ink-gray-8 outline-none focus:ring-0"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter.prevent="choose()"
        @keydown.esc.prevent="close"
      />
    </template>
    <template #actions>
      <span class="ed-meta w-16 text-right tabular-nums">{{ countLabel }}</span>
    </template>

    <div ref="list" class="min-h-0 flex-1 overflow-auto px-1.5 py-1">
      <div
        v-for="(p, i) in results"
        :key="p"
        :data-i="i"
        class="ed-row"
        :class="{ 'ed-row-selected': i === sel }"
        @click="choose(i)"
        @mousemove="sel = i"
      >
        <FileIcon :name="baseName(p)" class="ed-lane" />
        <span class="ed-name">{{ baseName(p) }}</span>
        <span class="ed-path">{{ dirName(p) }}</span>
      </div>
      <div v-if="!results.length" class="ed-empty">
        {{ loading ? 'Loading…' : 'No matching files' }}
      </div>
    </div>
  </Modal>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import FileIcon from '@/components/FileIcon.vue'
import Modal from '@/components/ui/Modal.vue'
import { filesApi } from '@/api/files'
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

const countLabel = computed(() => (results.value.length ? `${sel.value + 1}/${results.value.length}` : ''))

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
  editor.open(p, { preview: true }).catch(() => {})
}

async function loadFiles() {
  if (loadedOnce) return
  loading.value = true
  files.value = await filesApi.list()
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
