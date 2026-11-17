<script setup lang="ts">
import { Compartment, EditorState } from '@codemirror/state'
import { Decoration, EditorView, lineNumbers } from '@codemirror/view'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { loadLanguage } from '@/codemirror/languages'
import { viewerSetup } from '@/codemirror/setup'
import { useEditorStore } from '@/stores/editor'

const props = withDefaults(defineProps<{ content: string; path: string; line?: number }>(), {
  line: 1,
})

const store = useEditorStore()
const host = ref<HTMLElement>()
const hit = new Compartment()

let view: EditorView | undefined
let renders = 0

const hitLine = (from: number) =>
  EditorView.decorations.of(Decoration.set(Decoration.line({ class: 'cm-searchHit' }).range(from)))

const focusLine = () => {
  if (!view) return

  const { doc } = view.state
  const line = doc.line(Math.min(Math.max(1, props.line), doc.lines))

  view.dispatch({
    selection: { anchor: line.from },
    effects: [
      hit.reconfigure(hitLine(line.from)),
      EditorView.scrollIntoView(line.from, { y: 'center' }),
    ],
  })
}

const render = async () => {
  const run = ++renders
  const language = await loadLanguage(props.path)

  if (run !== renders || !view) return

  view.setState(
    EditorState.create({
      doc: props.content,
      extensions: [lineNumbers(), viewerSetup, hit.of([]), language?.extension ?? []],
    }),
  )
  focusLine()
}

onMounted(() => {
  view = new EditorView({ parent: host.value })
  render()
})

onBeforeUnmount(() => view?.destroy())

watch(() => [props.content, props.path], render)
watch(() => props.line, focusLine)
watch(
  () => store.fontSize,
  () => view?.requestMeasure(),
)
</script>

<template>
  <div ref="host" class="h-full w-full" />
</template>
