<template>
  <FrappeUIProvider>
    <div
      class="flex h-full flex-col-reverse overflow-hidden bg-surface-gray-1 font-sans text-ink-gray-8 antialiased sm:flex-row"
      @keydown.ctrl.s.prevent="saveActive"
      @keydown.meta.s.prevent="saveActive"
    >
      <ActivityBar :active="panel" :git-count="git.count" @select="select" />

      <aside
        v-show="panel"
        class="bg-panel fixed inset-0 z-30 flex w-full flex-col sm:static sm:inset-auto sm:z-auto sm:w-64 sm:border-r sm:border-outline-gray-1"
      >
        <div class="min-h-0 flex-1">
          <ExplorerPanel v-show="panel === 'files'" @close="panel = ''" />
          <SearchPanel v-if="panel === 'search'" @close="panel = ''" />
          <GitPanel v-if="panel === 'git'" @close="panel = ''" />
        </div>
      </aside>

      <div class="relative flex min-w-0 flex-1 flex-col">
        <Tabs v-show="!store.diff && !store.commit" @focus-files="panel = 'files'" />
        <div class="relative min-h-0 flex-1">
          <EditorPane v-show="!store.diff && !store.commit" class="h-full w-full" />
          <CommitView v-if="store.commit" class="fixed inset-0 z-40 sm:absolute sm:z-20" />
          <GitDiffView v-if="store.diff" class="fixed inset-0 z-40 sm:absolute sm:z-20" />
        </div>
      </div>

      <MobileNav
        class="sm:hidden"
        :active="panel"
        :git-count="git.count"
        :has-file="!!store.active"
        :editable="store.mobileEdit"
        :font-size="store.fontSize"
        @select="openPanel"
        @goto="openQuick"
        @find="openSearchModal"
        @toggle-edit="store.toggleMobileEdit()"
        @font="store.bumpFont($event)"
      />

      <QuickOpen />
      <SearchModal />
      <ConflictDialog />
      <PromptDialog />
      <ContextMenu />
      <Dialogs />
    </div>
  </FrappeUIProvider>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, defineAsyncComponent } from 'vue'
import { FrappeUIProvider, Dialogs } from 'frappe-ui'
import ActivityBar from '@/components/ActivityBar.vue'
import MobileNav from '@/components/MobileNav.vue'
import ExplorerPanel from '@/components/ExplorerPanel.vue'
import SearchPanel from '@/components/SearchPanel.vue'
import GitPanel from '@/components/GitPanel.vue'
import Tabs from '@/components/Tabs.vue'
import QuickOpen from '@/components/QuickOpen.vue'
import SearchModal from '@/components/SearchModal.vue'
import ConflictDialog from '@/components/ConflictDialog.vue'
import PromptDialog from '@/components/PromptDialog.vue'
import ContextMenu from '@/components/ContextMenu.vue'
import { api } from '@/api'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useTreeStore } from '@/stores/tree'
import { useQuickOpen } from '@/composables/useQuickOpen'
import { useSearchModal } from '@/composables/useSearchModal'
import { useMobile } from '@/composables/useMobile'

const EditorPane = defineAsyncComponent(() => import('@/components/EditorPane.vue'))
const GitDiffView = defineAsyncComponent(() => import('@/components/GitDiffView.vue'))
const CommitView = defineAsyncComponent(() => import('@/components/CommitView.vue'))

const store = useEditorStore()
const git = useGitStore()
const tree = useTreeStore()
const { openQuick } = useQuickOpen()
const { openSearchModal } = useSearchModal()
const { isMobile } = useMobile()
const panel = ref('files')

function select(id) {
  panel.value = panel.value === id ? '' : id
  if (panel.value === 'files') {
    if (store.diff) store.closeDiff()
    if (store.commit) store.closeCommit()
  }
}

// Mobile FAB: open the chosen panel and dismiss any open diff/commit overlay.
function openPanel(id) {
  panel.value = id
  if (store.diff) store.closeDiff()
  if (store.commit) store.closeCommit()
}

function saveActive() {
  if (store.active?.dirty) store.save(store.active.path)
}

function onKey(e) {
  const mod = e.ctrlKey || e.metaKey
  if (mod && e.shiftKey && (e.key === 'f' || e.key === 'F')) {
    e.preventDefault()
    openSearchModal()
  } else if (mod && !e.shiftKey && (e.key === 'p' || e.key === 'P')) {
    e.preventDefault()
    openQuick()
  }
}

let saveTimer = null
let savePending = false
function saveSession() {
  savePending = true
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    api.putState(store.snapshot())
    savePending = false
  }, 1000)
}
function onHidden() {
  if (document.hidden && savePending) {
    clearTimeout(saveTimer)
    api.putState(store.snapshot())
    savePending = false
  }
}

watch(
  () => [store.tabs.map((t) => t.path), store.activePath],
  () => saveSession(),
  { deep: false },
)

watch(
  () => store.openTick,
  () => {
    if (isMobile.value && panel.value) panel.value = ''
  },
)

onMounted(async () => {
  git.refresh()
  const params = new URLSearchParams(location.search)
  const p = params.get('panel')
  if (['files', 'search', 'git', ''].includes(p)) panel.value = p
  const open = params.get('open')
  const diff = params.get('diff')

  if (open) {
    store.open(open).catch(() => {})
  } else {
    try {
      await store.restore(await api.getState())
    } catch {}
  }
  if (diff) store.openDiff(diff)

  window.addEventListener('keydown', onKey)

  window.addEventListener('beforeunload', saveSession)
  document.addEventListener('visibilitychange', onHidden)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  clearTimeout(saveTimer)
  window.removeEventListener('beforeunload', saveSession)
  document.removeEventListener('visibilitychange', onHidden)
  saveSession()
})
</script>
