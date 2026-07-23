<template>
  <div class="flex h-full flex-col">
    <div
      class="group flex h-11 shrink-0 items-center justify-between border-b border-outline-gray-1 px-3 sm:h-9 sm:border-b-0"
      @contextmenu="onRootContext"
    >
      <span class="text-sm font-semibold text-ink-gray-8 sm:text-2xs sm:uppercase sm:tracking-wider sm:text-ink-gray-5">
        Explorer
      </span>
      <div class="flex items-center gap-0.5">
        <div class="flex items-center gap-0.5 transition sm:opacity-0 sm:group-hover:opacity-100">
          <button class="rounded p-1.5 text-ink-gray-6 hover:bg-surface-gray-2 sm:p-1" title="New file" @click="ops.newFile('')">
            <span class="lucide-file-plus h-[18px] w-[18px] text-current sm:h-3.5 sm:w-3.5"></span>
          </button>
          <button class="rounded p-1.5 text-ink-gray-6 hover:bg-surface-gray-2 sm:p-1" title="New folder" @click="ops.newFolder('')">
            <span class="lucide-folder-plus h-[18px] w-[18px] text-current sm:h-3.5 sm:w-3.5"></span>
          </button>
          <button class="rounded p-1.5 text-ink-gray-6 hover:bg-surface-gray-2 sm:p-1" title="Refresh" @click="refresh">
            <span class="lucide-refresh-cw h-[18px] w-[18px] text-current sm:h-3.5 sm:w-3.5"></span>
          </button>
        </div>
        <button class="ml-1 rounded p-1.5 text-ink-gray-6 hover:bg-surface-gray-2 sm:hidden" aria-label="Close" @click="emit('close')">
          <span class="lucide-x h-[18px] w-[18px] text-current"></span>
        </button>
      </div>
    </div>

    <div
      ref="scroller"
      tabindex="0"
      class="min-h-0 flex-1 overflow-auto px-1.5 pb-24 outline-none sm:pb-2"
      :class="{ 'bg-surface-gray-2': rootDragOver }"
      @contextmenu="onRootContext"
      @mousedown="onMousedown"
      @keydown="onKeydown"
      @dragover="onRootDragOver"
      @dragleave="rootDragOver = false"
      @drop="onRootDrop"
    >
      <div v-if="root.loading && !root.loaded" class="flex items-center gap-2 p-2 text-xs text-ink-gray-4">
        <Spinner class="h-3 w-3" /> loading
      </div>
      <TreeInput
        v-if="tree.draft?.parent === ''"
        :type="tree.draft.type"
        @commit="tree.commitDraft"
        @cancel="tree.cancelDraft"
      />
      <TreeNode v-for="c in root.children" :key="c.path" :entry="c" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, nextTick, watch } from 'vue'
import { Spinner } from 'frappe-ui'
import TreeNode from '@/components/TreeNode.vue'
import TreeInput from '@/components/TreeInput.vue'
import { useTreeStore, parentOf } from '@/stores/tree'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useContextMenu } from '@/composables/useContextMenu'
import { useFileOps } from '@/composables/useFileOps'

const emit = defineEmits(['close'])

const tree = useTreeStore()
const editor = useEditorStore()
const git = useGitStore()
const ops = useFileOps()
const { open } = useContextMenu()
const root = computed(() => tree.node(''))
const rootDragOver = ref(false)
const scroller = ref(null)

onMounted(() => {
  if (!root.value.loaded) tree.load('')
})

watch(
  () => editor.activePath,
  (path) => {
    if (!path) return
    tree.reveal(path).then(() => focusInto(path))
  }
)

function flatten(dir, depth, acc) {
  for (const c of tree.node(dir).children) {
    acc.push({ path: c.path, type: c.type, depth })
    if (c.type === 'dir' && tree.node(c.path).expanded) flatten(c.path, depth + 1, acc)
  }
  return acc
}
const flat = computed(() => flatten('', 0, []))

function focusInto(path) {
  tree.focusPath = path
  nextTick(() => {
    scroller.value?.querySelector(`[data-path="${cssEscape(path)}"]`)?.scrollIntoView({ block: 'nearest' })
  })
}
function cssEscape(s) {
  return s.replace(/["\\]/g, '\\$&')
}

function onMousedown(e) {
  if (e.target.closest('input, textarea')) return
  nextTick(() => scroller.value?.focus())
}

function onKeydown(e) {
  const list = flat.value
  if (!list.length) return
  let i = list.findIndex((x) => x.path === tree.focusPath)
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    focusInto(list[Math.min(list.length - 1, i + 1)].path)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    focusInto(list[i <= 0 ? 0 : i - 1].path)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    const node = list[i < 0 ? 0 : i]
    if (!node) return
    if (node.type === 'dir') tree.toggle(node.path)
    else {
      editor.closeDiff()
      editor.open(node.path, { preview: true }).catch(() => {})
    }
  } else if (e.key === 'ArrowRight') {
    const node = list[i]
    if (node?.type === 'dir' && !tree.node(node.path).expanded) tree.toggle(node.path)
  } else if (e.key === 'ArrowLeft') {
    const node = list[i]
    if (node?.type === 'dir' && tree.node(node.path).expanded) tree.toggle(node.path)
    else if (node) {
      const par = parentOf(node.path)
      if (par) focusInto(par)
    }
  }
}
function refresh() {
  tree.load('')
  git.refresh()
}
function onRootDragOver(e) {
  if (!tree.dragPath) return
  e.preventDefault()
  rootDragOver.value = true
}
function onRootDrop(e) {
  rootDragOver.value = false
  if (!tree.dragPath) return
  e.preventDefault()
  const from = tree.dragPath
  tree.dragPath = null
  tree.move(from, '').catch((err) => alert(err.message))
}
function onRootContext(e) {
  open(e, [
    { label: 'New File', icon: 'lucide-file-plus', action: () => ops.newFile('') },
    { label: 'New Folder', icon: 'lucide-folder-plus', action: () => ops.newFolder('') },
  ])
}
</script>
