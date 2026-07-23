<template>
  <div class="relative flex h-full flex-col">
    <div
      v-if="mergeConflicts.length"
      class="flex items-center gap-2 border-b border-outline-gray-2 bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-3"
    >
      <span class="lucide-alert-triangle h-4 w-4"></span>
      <span class="font-medium">{{ mergeConflicts.length }} unresolved conflict{{ mergeConflicts.length > 1 ? 's' : '' }}</span>
    </div>
    <div
      v-show="store.active"
      ref="host"
      class="absolute inset-0"
      :class="{ 'editor-readonly': readOnly, 'has-banner': mergeConflicts.length }"
    ></div>

    <div
      v-if="!store.active"
      class="flex h-full flex-col items-center justify-center gap-2 text-ink-gray-4"
    >
      <span class="lucide-file-text h-8 w-8 text-current"></span>
      <p class="text-sm">Open a file to start editing</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, watch, computed, nextTick } from 'vue'
import { monaco, languageFor } from '@/monaco'
import { useTheme } from '@/composables/useTheme'
import { useMobile } from '@/composables/useMobile'
import { useMergeConflicts } from '@/composables/useMergeConflicts'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { relTime } from '@/utils'

const store = useEditorStore()
const git = useGitStore()
const { isDark } = useTheme()
const { isMobile } = useMobile()
const host = ref(null)
const models = new Map()
const activeMergeHandler = ref(null)
const mergeConflicts = ref([])

const readOnly = computed(() => isMobile.value && !store.mobileEdit)
let editor = null
let applying = false
let blameDecos = null
let gitDecos = null
let blameData = []

function themeName() {
  return isDark.value ? 'vs-dark' : 'vs'
}

function modelFor(tab) {
  let m = models.get(tab.path)
  if (!m) {
    m = monaco.editor.createModel(tab.content, languageFor(tab.path))
    m.onDidChangeContent(() => {
      if (applying) return
      store.setContent(tab.path, m.getValue())
    })
    models.set(tab.path, m)
  }
  return m
}

function setActiveMergeHandler(tab) {
  if (activeMergeHandler.value) {
    activeMergeHandler.value.dispose()
    activeMergeHandler.value = null
  }
  if (!editor || !tab) return
  const m = models.get(tab.path)
  if (!m) return
  activeMergeHandler.value = useMergeConflicts(
    editor,
    m,
    (conflicts) => {
      mergeConflicts.value = conflicts || []
    },
    (conflicts) => {
      mergeConflicts.value = conflicts || []
      const t = store.tabs.find((t) => t.path === tab.path)
      if (t) {
        t.content = m.getValue()
        t.saved = m.getValue()
        t.dirty = false
      }
    },
    { isDark: isDark.value },
  )
}

function showActive() {
  if (!editor) return
  const tab = store.active
  if (!tab) return
  const m = modelFor(tab)
  if (editor.getModel() !== m) editor.setModel(m)
  setActiveMergeHandler(tab)
  if (store.blame) loadBlame()
  loadGitGutter()
}

async function loadGitGutter() {
  if (!editor || !store.active || !git.repo) {
    if (gitDecos) gitDecos.clear()
    return
  }
  const path = store.active.path
  const code = git.statusFor(path)
  let d
  if (code.includes('?')) {
    const n = editor.getModel()?.getLineCount() || 0
    d = { added: Array.from({ length: n }, (_, i) => i + 1), modified: [], deleted: [] }
  } else {
    d = await git.getDiff(path)
  }
  if (store.active && store.active.path === path) renderGitGutter(d)
}

function renderGitGutter(d) {
  if (!editor) return
  if (!gitDecos) gitDecos = editor.createDecorationsCollection()
  const model = editor.getModel()
  if (!model) return
  const max = model.getLineCount()
  const decos = []
  const add = (line, cls) => {
    if (line < 1 || line > max) return
    decos.push({
      range: new monaco.Range(line, 1, line, 1),
      options: { linesDecorationsClassName: cls },
    })
  }
  for (const l of d.added || []) add(l, 'git-gutter git-gutter-add')
  for (const l of d.modified || []) add(l, 'git-gutter git-gutter-mod')
  for (const l of d.deleted || []) add(Math.max(1, l), 'git-gutter git-gutter-del')
  gitDecos.set(decos)
}

async function loadBlame() {
  if (!store.active) return
  blameData = await git.getBlame(store.active.path)
  renderBlame()
}

function renderBlame() {
  if (!editor) return
  if (!blameDecos) blameDecos = editor.createDecorationsCollection()
  if (!store.blame || !blameData.length) {
    blameDecos.clear()
    return
  }
  const pos = editor.getPosition() || { lineNumber: 1, column: 1 }
  const b = blameData[pos.lineNumber - 1]
  const model = editor.getModel()
  if (!b || !model) {
    blameDecos.clear()
    return
  }
  const col = model.getLineMaxColumn(pos.lineNumber)
  const label = b.author === 'Uncommitted'
    ? '      Uncommitted changes'
    : `      ${b.author}, ${relTime(b.time)} • ${b.summary}`
  blameDecos.set([
    {
      range: new monaco.Range(pos.lineNumber, col, pos.lineNumber, col),
      options: {
        after: { content: label, inlineClassName: 'blame-inline' },
        showIfCollapsed: true,
      },
    },
  ])
}

onMounted(() => {
  editor = monaco.editor.create(host.value, {
    model: null,
    theme: themeName(),
    automaticLayout: true,
    readOnly: readOnly.value,
    domReadOnly: readOnly.value,
    fontSize: store.fontSize,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    smoothScrolling: true,
    renderWhitespace: 'selection',
    tabSize: 2,
    // Improve touch handling on phones/tablets.
    quickSuggestions: !isMobile.value,
    parameterHints: { enabled: !isMobile.value },
    hover: { enabled: !isMobile.value },
    suggestOnTriggerCharacters: !isMobile.value,
    acceptSuggestionOnEnter: isMobile.value ? 'off' : 'on',
  })
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    if (store.active?.dirty) store.save(store.active.path)
  })
  editor.onDidChangeCursorPosition((e) => {
    if (store.active) store.setCursor(store.active.path, e.position.lineNumber, e.position.column)
    if (store.blame) renderBlame()
  })
  if (new URLSearchParams(location.search).get('blame') === '1') store.blame = true
  host.value.addEventListener('wheel', onWheelZoom, { passive: false, capture: true })
  nextTick(() => {
    showActive()
    applyInputMode()
  })
})

// Tell mobile browsers not to raise the virtual keyboard while read-only, even
// if Monaco's hidden input element gets focused on tap.
function applyInputMode() {
  const input = host.value?.querySelector('.native-edit-context, textarea.inputarea')
  if (!input) return
  if (readOnly.value) input.setAttribute('inputmode', 'none')
  else input.removeAttribute('inputmode')
}

// Ctrl/Cmd + wheel (and trackpad pinch, which the browser maps to ctrl+wheel)
// steps the editor font size, keeping the store as the single source of truth.
function onWheelZoom(e) {
  if (!(e.ctrlKey || e.metaKey)) return
  e.preventDefault()
  store.bumpFont(e.deltaY < 0 ? 1 : -1)
}

onBeforeUnmount(() => {
  host.value?.removeEventListener('wheel', onWheelZoom, { capture: true })
  editor?.dispose()
  activeMergeHandler.value?.dispose()
  models.forEach((m) => m.dispose())
  models.clear()
})

watch(() => store.activePath, showActive)

watch(
  () => store.tabs.map((t) => t.path),
  (paths) => {
    for (const [p, m] of models) {
      if (!paths.includes(p)) {
        m.dispose()
        models.delete(p)
      }
    }
  },
)

watch(
  () => (store.active ? store.active.external : null),
  () => {
    const tab = store.active
    if (!tab) return
    const m = modelFor(tab)
    if (m.getValue() !== tab.content) {
      applying = true
      m.setValue(tab.content)
      applying = false
    }
  },
)

watch(
  () => store.reveal,
  (r) => {
    if (!r || !editor || !store.active || store.active.path !== r.path) return
    nextTick(() => {
      editor.revealLineInCenter(r.line)
      editor.setPosition({ lineNumber: r.line, column: r.col || 1 })
      editor.focus()
      if (store.blame) renderBlame()
    })
  },
)

watch(
  () => (store.active ? store.active.saved : null),
  () => {
    git.invalidateBlame(store.active?.path)
    if (store.blame) loadBlame()
    loadGitGutter()
  },
)

watch(
  () => git.repo + ':' + git.count,
  () => loadGitGutter(),
)

watch(
  () => store.blame,
  (on) => {
    if (on) loadBlame()
    else if (blameDecos) blameDecos.clear()
  },
)

watch(isDark, () => {
  monaco.editor.setTheme(themeName())
  // Re-render conflict zones so they pick up the new theme
  setActiveMergeHandler(store.active)
})

watch(isMobile, (mobile) => {
  if (!editor) return
  editor.updateOptions({
    quickSuggestions: !mobile,
    parameterHints: { enabled: !mobile },
    hover: { enabled: !mobile },
    suggestOnTriggerCharacters: !mobile,
    acceptSuggestionOnEnter: mobile ? 'off' : 'on',
  })
})

watch(
  () => store.fontSize,
  (size) => editor?.updateOptions({ fontSize: size }),
)

watch(readOnly, (ro) => {
  if (!editor) return
  editor.updateOptions({ readOnly: ro, domReadOnly: ro })
  applyInputMode()
  if (!ro) nextTick(() => editor.focus())
})

// When the diff overlay closes the editor was display:none, so Monaco needs a
// relayout to fill the area again.
watch(
  () => store.diff,
  (d) => {
    if (!d) nextTick(() => editor?.layout())
  },
)
</script>
