<script setup lang="ts">
import { FrappeUIProvider } from 'frappe-ui'
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { sessionApi } from '@/api/session'
import ActivityBar from '@/components/ActivityBar.vue'
import ConflictDialog from '@/components/ConflictDialog.vue'
import ContextMenu from '@/components/ContextMenu.vue'
import ExplorerPanel from '@/components/ExplorerPanel.vue'
import GitPanel from '@/components/GitPanel.vue'
import MobileNav from '@/components/MobileNav.vue'
import PromptDialog from '@/components/PromptDialog.vue'
import QuickOpen from '@/components/QuickOpen.vue'
import SearchModal from '@/components/SearchModal.vue'
import SearchPanel from '@/components/SearchPanel.vue'
import Tabs from '@/components/Tabs.vue'
import { useMobile } from '@/composables/useMobile'
import { useQuickOpen } from '@/composables/useQuickOpen'
import { useSearchModal } from '@/composables/useSearchModal'
import { HAS_GIT } from '@/scope'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'

type Panel = 'files' | 'search' | 'git' | ''

const EditorPane = defineAsyncComponent(() => import('@/components/EditorPane.vue'))
const GitDiffView = defineAsyncComponent(() => import('@/components/GitDiffView.vue'))
const CommitView = defineAsyncComponent(() => import('@/components/CommitView.vue'))

const panels = (['files', 'search', 'git', ''] as Panel[]).filter((it) => HAS_GIT || it !== 'git')

const store = useEditorStore()
const git = useGitStore()
const { openQuick } = useQuickOpen()
const { openSearchModal } = useSearchModal()
const { isMobile } = useMobile()
const panel = ref<Panel>('files')

const isPanel = (id: string | null): id is Panel => panels.includes(id as Panel)

const select = (id: string) => {
  if (!isPanel(id)) return

  panel.value = panel.value === id ? '' : id

  if (panel.value === 'files') {
    if (store.diff) store.closeDiff()
    if (store.commit) store.closeCommit()
  }
}

// Mobile FAB: open the chosen panel and dismiss any open diff/commit overlay.
const openPanel = (id: string) => {
  if (isPanel(id)) panel.value = id

  if (store.diff) store.closeDiff()
  if (store.commit) store.closeCommit()
}

const saveActive = () => {
  if (store.active?.dirty) store.save(store.active.path)
}

const onKey = (e: KeyboardEvent) => {
  const mod = e.ctrlKey || e.metaKey

  if (mod && e.shiftKey && (e.key === 'f' || e.key === 'F')) {
    e.preventDefault()
    openSearchModal()
  } else if (mod && !e.shiftKey && (e.key === 'p' || e.key === 'P')) {
    e.preventDefault()
    openQuick()
  }
}

let saveTimer: ReturnType<typeof setTimeout> | undefined
let savePending = false

const saveSession = () => {
  savePending = true
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    sessionApi.put(store.snapshot())
    savePending = false
  }, 1000)
}

// The debounced timer never fires once the page starts unloading, so write the
// snapshot synchronously (keepalive) on hide/unload instead.
const flushSession = () => {
  if (!savePending) return

  clearTimeout(saveTimer)
  sessionApi.put(store.snapshot())
  savePending = false
}

const onHidden = () => {
  if (document.hidden) flushSession()
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

watch(
  () => store.activePath,
  (path) => {
    if (isMobile.value && !path && !store.diff && !store.commit) panel.value = 'files'
  },
)

onMounted(async () => {
  git.refresh()

  const params = new URLSearchParams(location.search)
  const p = params.get('panel')
  const open = params.get('open')
  const diff = params.get('diff')

  if (isPanel(p)) panel.value = p

  if (open) {
    store.open(open).catch(() => {})
  } else {
    try {
      await store.restore(await sessionApi.get())
    } catch {}
  }

  if (diff) store.openDiff(diff)

  window.addEventListener('keydown', onKey)
  window.addEventListener('pagehide', flushSession)
  document.addEventListener('visibilitychange', onHidden)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('pagehide', flushSession)
  document.removeEventListener('visibilitychange', onHidden)
  flushSession()
})
</script>

<template>
  <FrappeUIProvider>
    <div
      class="flex h-full flex-col-reverse overflow-hidden bg-surface-gray-1 font-sans text-ink-gray-8 antialiased sm:flex-row"
      :style="{ '--editor-font-size': `${store.fontSize}px` }"
      @keydown.ctrl.s.prevent="saveActive"
      @keydown.meta.s.prevent="saveActive"
    >
      <ActivityBar :active="panel" :git-count="git.count" @select="select" />

      <aside
        v-show="panel"
        class="bg-panel fixed inset-0 z-30 flex w-full flex-col sm:static sm:inset-auto sm:z-auto sm:w-72 sm:border-r sm:border-outline-gray-1"
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
        @toggle-edit="store.toggleMobileEdit()"
        @font="store.bumpFont($event)"
      />

      <QuickOpen />
      <SearchModal />
      <ConflictDialog />
      <PromptDialog />
      <ContextMenu />
    </div>
  </FrappeUIProvider>
</template>
