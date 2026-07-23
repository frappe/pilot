<template>
  <div ref="host" class="h-full w-full"></div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue'
import { monaco, languageFor } from '@/monaco'
import { useTheme } from '@/composables/useTheme'
import { useEditorStore } from '@/stores/editor'

const props = defineProps({
  content: { type: String, default: '' },
  path: { type: String, default: '' },
  line: { type: Number, default: 1 },
})

const store = useEditorStore()
const { isDark } = useTheme()
const host = ref(null)
let editor = null
let model = null
let decorations = null

const themeName = () => (isDark.value ? 'vs-dark' : 'vs')

function setModel() {
  const next = monaco.editor.createModel(props.content, languageFor(props.path))
  editor.setModel(next)
  model?.dispose()
  model = next
  decorations = editor.createDecorationsCollection()
  highlight()
}

function highlight() {
  if (!editor || !model) return
  const line = Math.min(Math.max(1, props.line), model.getLineCount())
  decorations?.set([
    {
      range: new monaco.Range(line, 1, line, 1),
      options: { isWholeLine: true, className: 'search-hit-line' },
    },
  ])
  editor.setPosition({ lineNumber: line, column: 1 })
  // automaticLayout measures via a ResizeObserver that has not fired yet on
  // mount, so revealing would scroll against a zero-height viewport. Measure the
  // host ourselves first.
  const { width, height } = host.value.getBoundingClientRect()
  if (width && height) editor.layout({ width, height })
  editor.revealLineInCenter(line, monaco.editor.ScrollType.Immediate)
}

onMounted(() => {
  editor = monaco.editor.create(host.value, {
    theme: themeName(),
    readOnly: true,
    domReadOnly: true,
    automaticLayout: true,
    fontSize: store.fontSize,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    renderLineHighlight: 'none',
    overviewRulerLanes: 0,
    folding: false,
    occurrencesHighlight: 'off',
    contextmenu: false,
    scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
  })
  setModel()
})

onBeforeUnmount(() => {
  editor?.dispose()
  model?.dispose()
})

watch(() => [props.content, props.path], () => nextTick(setModel))
watch(() => props.line, highlight)
watch(isDark, () => monaco.editor.setTheme(themeName()))
watch(
  () => store.fontSize,
  (size) => editor?.updateOptions({ fontSize: size }),
)
</script>
