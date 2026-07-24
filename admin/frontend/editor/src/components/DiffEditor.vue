<template>
  <div ref="host" class="h-full w-full"></div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { monaco, languageFor, editorThemeName, applyEditorTheme } from '@/monaco'
import { useTheme } from '@/composables/useTheme'
import { useMobile } from '@/composables/useMobile'
import { useEditorStore } from '@/stores/editor'

const props = defineProps({
  original: { type: String, default: '' },
  modified: { type: String, default: '' },
  path: { type: String, default: '' },
})

const store = useEditorStore()
const { isDark } = useTheme()
const { isMobile } = useMobile()
const host = ref(null)
let editor = null
let originalModel = null
let modifiedModel = null

function setModels() {
  const lang = languageFor(props.path)
  originalModel?.dispose()
  modifiedModel?.dispose()
  originalModel = monaco.editor.createModel(props.original, lang)
  modifiedModel = monaco.editor.createModel(props.modified, lang)
  editor?.setModel({ original: originalModel, modified: modifiedModel })
}

onMounted(() => {
  editor = monaco.editor.createDiffEditor(host.value, {
    theme: editorThemeName(),
    readOnly: true,
    automaticLayout: true,
    fontSize: store.fontSize,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    renderOverviewRuler: false,
    renderSideBySide: !isMobile.value,
  })
  setModels()
})

onBeforeUnmount(() => {
  editor?.dispose()
  originalModel?.dispose()
  modifiedModel?.dispose()
})

watch(() => [props.original, props.modified, props.path], setModels)
watch(isDark, applyEditorTheme)
watch(() => store.fontSize, (s) => editor?.updateOptions({ fontSize: s }))
watch(isMobile, (m) => editor?.updateOptions({ renderSideBySide: !m }))
</script>
