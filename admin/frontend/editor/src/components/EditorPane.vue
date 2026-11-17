<script setup lang="ts">
import { autocompletion } from '@codemirror/autocomplete'
import { foldGutter } from '@codemirror/language'
import { Annotation, Compartment, EditorState } from '@codemirror/state'
import { EditorView, lineNumbers } from '@codemirror/view'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { blame, gitGutter, type LineChanges, setBlame, setLineChanges } from '@/codemirror/git'
import { loadLanguage } from '@/codemirror/languages'
import { conflictCount, mergeConflicts } from '@/codemirror/mergeConflicts'
import { editorSetup } from '@/codemirror/setup'
import { useMobile } from '@/composables/useMobile'
import { type Reveal, useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'

const store = useEditorStore()
const git = useGitStore()
const { isMobile } = useMobile()
const host = ref<HTMLElement>()
const conflicts = ref(0)
const states = new Map<string, EditorState>()
const editable = new Compartment()
const completion = new Compartment()
const external = Annotation.define<boolean>()

const readOnly = computed(() => isMobile.value && !store.mobileEdit)

let view: EditorView | undefined
let shownPath: string | null = null
let revealed: Reveal | null = null

const editableExtension = () => [
  EditorState.readOnly.of(readOnly.value),
  EditorView.editable.of(!readOnly.value),
]

const completionExtension = () => autocompletion({ activateOnTyping: !isMobile.value })

const reconfigure = () =>
  view?.dispatch({
    effects: [
      editable.reconfigure(editableExtension()),
      completion.reconfigure(completionExtension()),
    ],
  })

const syncToStore = EditorView.updateListener.of((update) => {
  if (!shownPath) return

  const isExternal = update.transactions.some((transaction) => transaction.annotation(external))

  if (update.docChanged && !isExternal) store.setContent(shownPath, update.state.doc.toString())

  if (update.selectionSet) {
    const head = update.state.selection.main.head
    const line = update.state.doc.lineAt(head)

    store.setCursor(shownPath, line.number, head - line.from + 1)
  }

  conflicts.value = conflictCount(update.state)
})

const createState = async (path: string, content: string) => {
  const language = await loadLanguage(path)

  return EditorState.create({
    doc: content,
    extensions: [
      lineNumbers(),
      gitGutter,
      foldGutter(),
      editorSetup,
      blame,
      mergeConflicts,
      editable.of(editableExtension()),
      completion.of(completionExtension()),
      syncToStore,
      language?.extension ?? [],
    ],
  })
}

const syncDoc = () => {
  const tab = store.active

  if (!view || !tab || tab.path !== shownPath || view.state.doc.toString() === tab.content) return

  view.dispatch({
    changes: { from: 0, to: view.state.doc.length, insert: tab.content },
    annotations: external.of(true),
  })
}

const lineChanges = async (path: string): Promise<LineChanges> => {
  if (!git.repo) return {}

  if (!git.statusFor(path).includes('?')) return git.getDiff(path)

  const lines = view?.state.doc.lines ?? 0

  return { added: Array.from({ length: lines }, (_, index) => index + 1) }
}

const loadGitGutter = async () => {
  const path = shownPath

  if (!path) return

  const changes = await lineChanges(path)

  if (path === shownPath) view?.dispatch({ effects: setLineChanges.of(changes) })
}

const loadBlame = async () => {
  const path = shownPath

  if (!path) return

  const lines = store.blame ? await git.getBlame(path) : []

  if (path === shownPath) view?.dispatch({ effects: setBlame.of(lines) })
}

const applyReveal = () => {
  const reveal = store.reveal

  if (!view || !reveal || reveal === revealed || reveal.path !== shownPath) return

  const { doc } = view.state
  const line = doc.line(Math.min(Math.max(1, reveal.line), doc.lines))
  const anchor = Math.min(line.from + reveal.col - 1, line.to)

  revealed = reveal
  view.dispatch({
    selection: { anchor },
    effects: EditorView.scrollIntoView(anchor, { y: 'center' }),
  })
  view.focus()
}

const showActive = async () => {
  const tab = store.active

  if (!view || !tab || tab.path === shownPath) return

  const state = states.get(tab.path) ?? (await createState(tab.path, tab.content))

  if (!view || store.active?.path !== tab.path) return

  if (shownPath && states.has(shownPath)) states.set(shownPath, view.state)

  states.set(tab.path, state)
  shownPath = tab.path
  view.setState(state)
  reconfigure()
  syncDoc()
  conflicts.value = conflictCount(view.state)
  loadGitGutter()
  loadBlame()
  applyReveal()
}

const zoom = (event: WheelEvent) => {
  if (!event.ctrlKey && !event.metaKey) return

  event.preventDefault()
  store.bumpFont(event.deltaY < 0 ? 1 : -1)
}

onMounted(() => {
  if (new URLSearchParams(location.search).get('blame') === '1') store.blame = true

  view = new EditorView({ parent: host.value })
  showActive()
})

onBeforeUnmount(() => {
  view?.destroy()
  view = undefined
  states.clear()
})

watch(() => store.activePath, showActive)
watch(() => store.active?.external, syncDoc)
watch(() => store.reveal, applyReveal)
watch(() => store.blame, loadBlame)
watch(() => `${git.repo}:${git.count}`, loadGitGutter)
watch(isMobile, reconfigure)

watch(
  () => store.tabs.map((tab) => tab.path),
  (paths) => {
    for (const path of states.keys()) if (!paths.includes(path)) states.delete(path)

    if (shownPath && !paths.includes(shownPath)) shownPath = null
  },
)

watch(
  () => store.active?.saved,
  () => {
    if (store.active) git.invalidateBlame(store.active.path)

    loadBlame()
    loadGitGutter()
  },
)

watch(readOnly, (value) => {
  reconfigure()

  if (!value) view?.focus()
})

watch(
  () => store.fontSize,
  () => view?.requestMeasure(),
)
</script>

<template>
  <div class="flex h-full flex-col">
    <p
      v-if="conflicts"
      class="flex items-center gap-2 border-b border-outline-gray-2 bg-surface-amber-1 px-3 py-2 text-sm-medium text-ink-amber-5"
    >
      <span class="lucide-alert-triangle h-4 w-4" />
      {{ conflicts }}
      unresolved conflict{{ conflicts > 1 ? 's' : '' }}
    </p>

    <div v-show="store.active" ref="host" class="min-h-0 flex-1" @wheel.capture="zoom" />

    <p
      v-if="!store.active"
      class="flex h-full flex-col items-center justify-center gap-2 text-sm text-ink-gray-4"
    >
      <span class="lucide-file-text h-8 w-8" />
      Open a file to start editing
    </p>
  </div>
</template>
