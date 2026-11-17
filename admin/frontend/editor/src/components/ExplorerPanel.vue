<script setup lang="ts">
import { Spinner, toast } from 'frappe-ui'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import TreeInput from '@/components/TreeInput.vue'
import TreeNode from '@/components/TreeNode.vue'
import PanelHeader from '@/components/ui/PanelHeader.vue'
import { useContextMenu } from '@/composables/useContextMenu'
import { useFileOps } from '@/composables/useFileOps'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { parentOf, useTreeStore } from '@/stores/tree'

interface FlatEntry {
  path: string
  type: string
  depth: number
}

const emit = defineEmits<{ close: [] }>()

const tree = useTreeStore()
const editor = useEditorStore()
const git = useGitStore()
const ops = useFileOps()
const { open } = useContextMenu()
const root = computed(() => tree.node(''))
const rootDragOver = ref(false)
const scroller = ref<HTMLElement>()

const flatten = (dir: string, depth: number, acc: FlatEntry[]): FlatEntry[] => {
  for (const c of tree.node(dir).children) {
    acc.push({ path: c.path, type: c.type, depth })

    if (c.type === 'dir' && tree.node(c.path).expanded) flatten(c.path, depth + 1, acc)
  }

  return acc
}

const flat = computed(() => flatten('', 0, []))

const cssEscape = (s: string) => s.replace(/["\\]/g, '\\$&')

const focusInto = (path: string) => {
  tree.focusPath = path
  nextTick(() => {
    scroller.value
      ?.querySelector(`[data-path="${cssEscape(path)}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  })
}

const onMousedown = (e: MouseEvent) => {
  if ((e.target as HTMLElement).closest('input, textarea')) return

  nextTick(() => scroller.value?.focus())
}

const onKeydown = (e: KeyboardEvent) => {
  const list = flat.value

  if (!list.length) return

  const i = list.findIndex((x) => x.path === tree.focusPath)

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

const refresh = () => {
  tree.load('')
  git.refresh()
}

const onRootDragOver = (e: DragEvent) => {
  if (!tree.dragPath) return

  e.preventDefault()
  rootDragOver.value = true
}

const onRootDrop = (e: DragEvent) => {
  rootDragOver.value = false

  if (!tree.dragPath) return

  e.preventDefault()

  const from = tree.dragPath

  tree.dragPath = null
  tree.move(from, '').catch((err: Error) => toast.error(err.message))
}

const onRootContext = (e: MouseEvent) => {
  open(e, [
    { label: 'New File', icon: 'lucide-file-plus', action: () => ops.newFile('') },
    { label: 'New Folder', icon: 'lucide-folder-plus', action: () => ops.newFolder('') },
  ])
}

onMounted(() => {
  if (!root.value.loaded) tree.load('')
})

watch(
  () => editor.activePath,
  (path) => {
    if (!path) return

    tree.reveal(path).then(() => focusInto(path))
  },
)
</script>

<template>
  <div class="flex h-full flex-col">
    <PanelHeader title="Explorer" @close="emit('close')" @contextmenu="onRootContext">
      <template #actions>
        <button class="ed-icon-button" title="New file" @click="ops.newFile('')">
          <span class="lucide-file-plus h-4 w-4 text-current"></span>
        </button>
        <button class="ed-icon-button" title="New folder" @click="ops.newFolder('')">
          <span class="lucide-folder-plus h-4 w-4 text-current"></span>
        </button>
        <button class="ed-icon-button" title="Refresh" @click="refresh">
          <span class="lucide-refresh-cw h-4 w-4 text-current"></span>
        </button>
      </template>
    </PanelHeader>

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
      <div
        v-if="root.loading && !root.loaded"
        class="ed-empty flex items-center justify-center gap-2"
      >
        <Spinner class="h-3 w-3" />
        Loading…
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
