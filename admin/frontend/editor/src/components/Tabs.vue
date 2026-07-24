<template>
  <div
    v-if="store.tabs.length"
    class="bg-chrome flex h-10 shrink-0 items-stretch border-b border-outline-gray-1 sm:h-9"
  >
    <div
      class="flex min-w-0 flex-1 items-stretch overflow-x-auto no-scrollbar"
      @wheel="onWheel"
    >
      <div
        v-for="t in store.tabs"
        :key="t.path"
        class="group flex min-w-0 flex-shrink-0 cursor-pointer items-center gap-2 border-r border-outline-gray-1 pl-3 pr-2 text-sm"
        :class="{
          'bg-panel text-ink-gray-9': t.path === store.activePath && !store.diff,
          'text-ink-gray-6 hover:bg-surface-gray-3': t.path !== store.activePath || store.diff,
        }"
        :title="t.path"
        @click="onTabClick(t)"
        @dblclick="store.pin(t.path)"
        @mousedown.middle.prevent="close(t)"
        @contextmenu.stop.prevent="tabMenu($event, t)"
        @touchstart="onTouchStart($event, t)"
        @touchmove="onTouchMove"
        @touchend="onTouchEnd"
      >
        <FileIcon :name="t.name" class="ed-lane" />
        <span class="truncate" :class="{ 'text-ink-amber-3': t.dirty, italic: t.preview }">{{ t.name }}</span>
        <span
          class="flex h-6 w-6 items-center justify-center rounded hover:bg-surface-gray-4 sm:h-4 sm:w-4"
          @click.stop="close(t)"
        >
          <span
            v-if="t.dirty"
            class="h-2 w-2 rounded-full bg-ink-amber-3 group-hover:hidden"
            title="Unsaved changes"
          ></span>
          <span
            class="lucide-x h-3 w-3 text-current"
            :class="{ 'hidden group-hover:block': t.dirty }"
          ></span>
        </span>
      </div>
    </div>

    <div class="flex shrink-0 items-center gap-1 border-l border-outline-gray-1 px-2">
      <button
        v-if="store.active?.dirty"
        class="rounded-md px-2 py-1 text-xs font-medium text-ink-amber-3 sm:hidden"
        @click="store.save(store.active.path)"
      >
        Save
      </button>
      <span
        v-if="dirtyCount && !store.autosave"
        class="hidden items-center gap-1 text-xs text-ink-amber-3 sm:flex"
        :title="`${dirtyCount} unsaved file(s)`"
      >
        <span class="h-2 w-2 rounded-full bg-ink-amber-3"></span>
        {{ dirtyCount }} unsaved
      </span>
      <button
        class="rounded-md px-1.5 py-1 text-xs"
        :class="store.autosave ? 'text-ink-green-3' : 'text-ink-gray-5 hover:text-ink-gray-8'"
        :title="store.autosave ? 'Auto-save on' : 'Auto-save off'"
        @click="store.toggleAutosave()"
      >
        Auto-save
      </button>
      <button
        class="rounded-md px-1.5 py-1 text-xs"
        :class="store.blame ? 'text-ink-green-3' : 'text-ink-gray-5 hover:text-ink-gray-8'"
        :title="store.blame ? 'Blame on' : 'Blame off'"
        @click="store.toggleBlame()"
      >
        Blame
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { confirmDialog } from 'frappe-ui'
import FileIcon from '@/components/FileIcon.vue'
import { useEditorStore } from '@/stores/editor'
import { useContextMenu } from '@/composables/useContextMenu'

const emit = defineEmits(['focus-files'])

const store = useEditorStore()
const { open } = useContextMenu()
const longPressTimer = ref(null)
let longPressTriggered = false

const dirtyCount = computed(() => store.tabs.filter((t) => t.dirty).length)

function onWheel(e) {
  if (e.deltaY === 0) return
  e.currentTarget.scrollLeft += e.deltaY
  e.preventDefault()
}

function close(t) {
  if (!t.dirty) {
    store.close(t.path)
    return
  }
  confirmDialog({
    title: 'Discard changes',
    message: `Discard unsaved changes to ${t.name}?`,
    onConfirm: ({ hideDialog }) => {
      hideDialog()
      store.close(t.path)
    },
  })
}

function tabMenu(e, t) {
  open(e, [
    { label: 'Close', icon: 'lucide-x', action: () => close(t) },
    { label: 'Close Others', icon: 'lucide-chevrons-left', action: () => store.closeOthers(t.path) },
    { label: 'Close Saved', icon: 'lucide-check', action: () => store.closeSaved() },
    { label: 'Close All', icon: 'lucide-x-square', action: () => store.closeAll() },
  ])
}

function onTouchStart(e, t) {
  clearTimeout(longPressTimer.value)
  longPressTriggered = false
  longPressTimer.value = setTimeout(() => {
    longPressTriggered = true
    tabMenu(e.touches[0] || e, t)
  }, 500)
}
function onTouchMove() {
  clearTimeout(longPressTimer.value)
}
function onTouchEnd() {
  clearTimeout(longPressTimer.value)
}
function onTabClick(t) {
  if (longPressTriggered) {
    longPressTriggered = false
    return
  }
  store.closeDiff()
  store.activePath = t.path
  emit('focus-files')
}
</script>
