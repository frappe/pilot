<template>
  <div>
    <TreeInput
      v-if="editing"
      :type="isDir ? 'dir' : 'file'"
      :initial="entry.name"
      @commit="(name) => tree.commitRename(entry.path, name)"
      @cancel="tree.cancelRename"
    />

    <div
      v-else
      :data-path="entry.path"
      class="group/row relative flex cursor-pointer select-none items-center gap-1.5 rounded-md p-1.5 text-sm leading-tight sm:py-1"
      :class="{
        'text-ink-gray-9': active,
        'text-ink-gray-7': !active,
        'hover:bg-surface-gray-2': true,
        'ring-1 ring-inset ring-outline-gray-3': focused,
        'ring-1 ring-inset ring-outline-gray-4 bg-surface-gray-2': dragOver,
        'opacity-50': entry.ignored,
      }"
      draggable="true"
      @click="onClick"
      @dblclick="onDblClick"
      @contextmenu.stop="onContext"
      @touchstart="onTouchStart"
      @touchend="onTouchEnd"
      @touchmove="onTouchMove"
      @dragstart="onDragStart"
      @dragend="tree.dragPath = null"
      @dragover="onDragOver"
      @dragleave="dragOver = false"
      @drop="onDrop"
    >
      <span
        v-if="active"
        class="absolute inset-y-1 right-0 w-0.5 rounded-full bg-surface-gray-7"
      ></span>
      <span
        v-if="isDir"
        :class="node.expanded ? 'lucide-chevron-down' : 'lucide-chevron-right'"
        class="h-4 w-4 shrink-0 text-ink-gray-4 sm:h-3.5 sm:w-3.5"
      ></span>
      <span v-else class="w-4 shrink-0 sm:w-3.5"></span>
      <FileIcon
        :name="entry.name"
        :dir="isDir"
        :open="isDir && node.expanded"
        class="h-4 w-4 shrink-0"
      />
      <span class="truncate" :class="statusColor">{{ entry.name }}</span>
      <span class="ml-auto flex items-center gap-1 pl-1">
        <span v-if="status" class="text-2xs font-semibold" :class="statusColor">
          {{ status }}
        </span>
      </span>
    </div>

    <div v-if="isDir && node.expanded" class="ml-[15px] border-l border-outline-gray-1">
      <TreeInput
        v-if="draftHere"
        :type="tree.draft.type"
        @commit="tree.commitDraft"
        @cancel="tree.cancelDraft"
      />
      <div
        v-if="node.loading"
        class="flex items-center gap-1.5 py-2 pl-2 text-xs text-ink-gray-4 sm:py-1"
      >
        <Spinner class="h-3 w-3" /> loading
      </div>
      <TreeNode v-for="c in node.children" :key="c.path" :entry="c" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Spinner, toast } from 'frappe-ui'
import FileIcon from '@/components/FileIcon.vue'
import TreeInput from '@/components/TreeInput.vue'
import { useTreeStore } from '@/stores/tree'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useContextMenu } from '@/composables/useContextMenu'
import { useFileOps } from '@/composables/useFileOps'

const props = defineProps({ entry: { type: Object, required: true } })

const tree = useTreeStore()
const editor = useEditorStore()
const git = useGitStore()
const { open } = useContextMenu()
const ops = useFileOps()

const dragOver = ref(false)
const longPressTimer = ref(null)
let longPressTriggered = false

const isDir = computed(() => props.entry.type === 'dir')
const node = computed(() => tree.node(props.entry.path))
const active = computed(() => !isDir.value && editor.activePath === props.entry.path)
const focused = computed(() => tree.focusPath === props.entry.path)
const editing = computed(() => tree.editing === props.entry.path)
const draftHere = computed(() => tree.draft?.parent === props.entry.path)

const status = computed(() => {
  if (isDir.value) {
    const pfx = props.entry.path + '/'
    return Object.keys(git.files).some((p) => p.startsWith(pfx)) ? '•' : ''
  }
  const c = git.statusFor(props.entry.path)
  if (!c) return ''
  if (c.includes('?')) return 'U'
  return c[0]
})

const statusColor = computed(() => {
  const s = status.value
  if (!s) return ''
  if (s === 'U' || s === 'A') return 'text-ink-green-3'
  if (s === 'D') return 'text-ink-red-4'
  return 'text-ink-amber-3'
})

async function onClick() {
  if (longPressTriggered) {
    longPressTriggered = false
    return
  }
  tree.focusPath = props.entry.path
  if (isDir.value) tree.toggle(props.entry.path)
  else openFile(true)
}

function onDblClick() {
  if (!isDir.value) openFile(false)
}

async function openFile(preview) {
  try {
    editor.closeDiff()
    await editor.open(props.entry.path, { preview })
  } catch (e) {
    toast.error(e.message)
  }
}

function onTouchStart(e) {
  clearTimeout(longPressTimer.value)
  longPressTriggered = false
  longPressTimer.value = setTimeout(() => {
    longPressTriggered = true
    onContext(e.touches[0] || e)
  }, 500)
}
function onTouchMove() {
  clearTimeout(longPressTimer.value)
}
function onTouchEnd() {
  clearTimeout(longPressTimer.value)
}

function onDragStart(e) {
  tree.dragPath = props.entry.path
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', props.entry.path)
}
function onDragOver(e) {
  if (!isDir.value || !tree.dragPath || tree.dragPath === props.entry.path) return
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  dragOver.value = true
}
function onDrop(e) {
  dragOver.value = false
  if (!isDir.value || !tree.dragPath) return
  e.preventDefault()
  e.stopPropagation()
  const from = tree.dragPath
  tree.dragPath = null
  tree.move(from, props.entry.path).catch((err) => toast.error(err.message))
}

function onContext(e) {
  clearTimeout(longPressTimer.value)
  const p = props.entry.path
  const baseItems = [
    { label: 'Rename', icon: 'lucide-pencil', action: () => ops.rename(p) },
    { label: 'Delete', icon: 'lucide-trash-2', danger: true, action: () => ops.remove(p, isDir.value) },
  ]
  const items = isDir.value
    ? [
        { label: 'New File', icon: 'lucide-file-plus', action: () => ops.newFile(p) },
        { label: 'New Folder', icon: 'lucide-folder-plus', action: () => ops.newFolder(p) },
        ...baseItems,
      ]
    : baseItems
  open(e, items)
}
</script>
