<script setup lang="ts">
import { autocompletion, type CompletionSource } from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { pythonLanguage } from '@codemirror/lang-python'
import { LanguageSupport, syntaxHighlighting } from '@codemirror/language'
import { Prec } from '@codemirror/state'
import {
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  placeholder,
  tooltips,
} from '@codemirror/view'
import { styleTags, tags } from '@lezer/highlight'
import { computed } from 'vue'
import { Codemirror } from 'vue-codemirror'

import {
  browserFind,
  codeEditorHighlightStyle,
  codeEditorTheme,
  codeTags,
  consoleEditorTheme,
} from '@/components/common/codeEditorTheme'
import { completionKinds } from '@/components/common/completionKinds'
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

const python = pythonLanguage.configure({
  props: [
    styleTags({
      'for while': codeTags.repeat,
      'try except finally raise': codeTags.exception,
      from: tags.moduleKeyword,
      async: tags.keyword,
      '"*" "**"': tags.operator,
      'ParamList/VariableName': codeTags.parameter,
      'FormatSpec FormatConversion': tags.escape,
    }),
  ],
})

const extensions = [
  consoleEditorTheme,
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
  new LanguageSupport(python),
  // Replaces the Python language's own word list; Jedi covers locals and builtins.
  autocompletion({
    ...completionKinds,
    maxRenderedOptions: 30,
    ...(props.completions && { override: [props.completions] }),
  }),
  // Outside the editor card, whose overflow-hidden would clip the docs panel.
  tooltips({ parent: document.body }),
  runKeymap,
  browserFind,
]
</script>

<template>
  <Codemirror v-model="model" :extensions="extensions" :autofocus="true" />
</template>
