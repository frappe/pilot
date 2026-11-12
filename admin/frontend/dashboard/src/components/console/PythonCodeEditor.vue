<script setup lang="ts">
import { computed } from 'vue'
import { Codemirror } from 'vue-codemirror'
import { autocompletion, type CompletionSource } from '@codemirror/autocomplete'
import { python } from '@codemirror/lang-python'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { syntaxHighlighting } from '@codemirror/language'
import { Prec } from '@codemirror/state'
import {
  tooltips,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  placeholder,
} from '@codemirror/view'

import { codeEditorHighlightStyle, codeEditorTheme } from '@/components/common/codeEditorTheme'
import { fillLinesOnResize } from '@/components/common/fillLinesOnResize'

const props = defineProps<{
  modelValue: string
  completions?: CompletionSource
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; run: [] }>()

const model = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const runKeymap = Prec.highest(
  keymap.of([
    {
      key: 'Ctrl-Enter',
      mac: 'Cmd-Enter',
      run: () => {
        emit('run')
        return true
      },
    },
  ]),
)

const extensions = [
  codeEditorTheme,
  fillLinesOnResize,
  Prec.highest(syntaxHighlighting(codeEditorHighlightStyle)),
  lineNumbers(),
  drawSelection(),
  highlightActiveLine(),
  highlightActiveLineGutter(),
  history(),
  keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
  placeholder('print(frappe.get_all("User", fields=["name", "enabled"], limit=5))'),
  python(),
  // Replaces the Python language's own word list; Jedi covers locals and builtins.
  autocompletion({
    icons: false,
    maxRenderedOptions: 30,
    ...(props.completions && { override: [props.completions] }),
  }),
  // Outside the editor card, whose overflow-hidden would clip the docs panel.
  tooltips({ parent: document.body }),
  runKeymap,
]
</script>

<template>
  <Codemirror v-model="model" :extensions="extensions" :autofocus="true" />
</template>
