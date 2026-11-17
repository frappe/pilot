<script setup lang="ts">
import { MergeView, unifiedMergeView } from '@codemirror/merge'
import { EditorView, lineNumbers } from '@codemirror/view'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { loadLanguage } from '@/codemirror/languages'
import { viewerSetup } from '@/codemirror/setup'
import { useMobile } from '@/composables/useMobile'
import { useEditorStore } from '@/stores/editor'

const props = defineProps<{ original: string; modified: string; path: string }>()

const store = useEditorStore()
const { isMobile } = useMobile()
const host = ref<HTMLElement>()

let diff: MergeView | EditorView | undefined
let renders = 0

const render = async () => {
  const run = ++renders
  const language = await loadLanguage(props.path)

  if (run !== renders || !host.value) return

  const extensions = [lineNumbers(), viewerSetup, language?.extension ?? []]

  diff?.destroy()
  diff = isMobile.value
    ? new EditorView({
        parent: host.value,
        doc: props.modified,
        extensions: [
          ...extensions,
          unifiedMergeView({ original: props.original, mergeControls: false }),
        ],
      })
    : new MergeView({
        parent: host.value,
        a: { doc: props.original, extensions },
        b: { doc: props.modified, extensions },
      })
}

onMounted(render)

onBeforeUnmount(() => {
  renders++
  diff?.destroy()
})

watch(() => [props.original, props.modified, props.path, isMobile.value], render)
watch(
  () => store.fontSize,
  () => {
    const views = diff instanceof MergeView ? [diff.a, diff.b] : [diff]

    for (const view of views) view?.requestMeasure()
  },
)
</script>

<template>
  <div ref="host" class="h-full w-full" />
</template>
