<template>
  <Codemirror
    v-model="model"
    :extensions="extensions"
    :autofocus="true"
    :style="{ height: '100%' }"
    @ready="onReady"
  />
</template>

<script setup>
import { computed, shallowRef, watch } from 'vue'
import { Codemirror } from 'vue-codemirror'
import { MariaSQL, PostgreSQL, SQLite as SQLiteDialect, sql } from '@codemirror/lang-sql'
import { autocompletion } from '@codemirror/autocomplete'
import { history, historyKeymap, defaultKeymap, indentWithTab } from '@codemirror/commands'
import {
  keymap, EditorView, lineNumbers, drawSelection,
  highlightActiveLine, highlightActiveLineGutter, placeholder,
} from '@codemirror/view'
import { Compartment, Prec } from '@codemirror/state'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags } from '@lezer/highlight'

const props = defineProps({
  modelValue: { type: String, default: '' },
  schema: { type: Array, default: () => [] },
  dbType: { type: String, default: 'mariadb' },
})

const emit = defineEmits(['update:modelValue', 'run'])

const model = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const view = shallowRef(null)

function onReady({ view: v }) {
  view.value = v
}

const cmSchema = computed(() => {
  const s = {}
  for (const table of props.schema) {
    s[table.name] = table.columns.map((c) => ({ label: c.name, type: 'property', detail: c.type }))
  }
  return s
})

const dialects = { mariadb: MariaSQL, postgres: PostgreSQL, sqlite: SQLiteDialect }
const cmDialect = computed(() => dialects[props.dbType] || MariaSQL)

const sqlCompartment = new Compartment()

function reconfigureSql() {
  if (!view.value) return
  view.value.dispatch({
    effects: sqlCompartment.reconfigure(
      sql({ dialect: cmDialect.value, upperCaseKeywords: true, schema: cmSchema.value }),
    ),
  })
}

watch([cmSchema, cmDialect], reconfigureSql)

function getSelectedOrAll(v) {
  const { from, to } = v.state.selection.main
  return from !== to ? v.state.sliceDoc(from, to) : v.state.doc.toString()
}

function getQueryToRun() {
  return view.value ? getSelectedOrAll(view.value) : props.modelValue
}

const theme = EditorView.theme({
  '&': {
    height: '100%',
    fontSize: '13px',
    color: 'var(--ink-gray-8, #1e293b)',
    backgroundColor: 'var(--surface-base, white)',
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    lineHeight: '22px',
    overflow: 'auto',
  },
  '.cm-content': {
    caretColor: 'var(--ink-gray-8, #1e293b)',
    padding: '8px 0',
  },
  '.cm-line': { padding: '0 14px' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--ink-gray-8, #1e293b)' },
  // No padding on .cm-gutters - CodeMirror positions gutter elements from the
  // content line coordinates; adding padding here misaligns them.
  '.cm-gutters': {
    backgroundColor: 'var(--surface-gray-1, #f8fafc)',
    borderRight: '1px solid var(--outline-gray-2, #e8eaed)',
    color: 'var(--ink-gray-4, #9aa0a6)',
  },
  '.cm-activeLine': {
    backgroundColor: 'color-mix(in srgb, var(--ink-gray-9, #0f0f0f) 4%, transparent)',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'color-mix(in srgb, var(--ink-gray-9, #0f0f0f) 4%, transparent)',
    color: 'var(--ink-gray-6, #5f6368)',
  },
  '.cm-selectionBackground': { backgroundColor: 'rgba(66, 133, 244, 0.18)' },
  '&.cm-focused .cm-selectionBackground': { backgroundColor: 'rgba(66, 133, 244, 0.25)' },
  '.cm-placeholder': { color: 'var(--ink-gray-4, #9aa0a6)', fontStyle: 'normal' },
  '.cm-tooltip': {
    backgroundColor: 'var(--surface-elevation-2, white)',
    border: '1px solid var(--outline-gray-2, #e8eaed)',
    borderRadius: '6px',
    color: 'var(--ink-gray-8, #1e293b)',
    boxShadow: '0 2px 8px rgba(0,0,0,0.10)',
    overflow: 'hidden',
  },
  '.cm-tooltip-autocomplete > ul': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    fontSize: '12px',
  },
  '.cm-tooltip-autocomplete > ul > li': { color: 'var(--ink-gray-7, #475569)', padding: '3px 8px' },
  '.cm-tooltip-autocomplete > ul > li[aria-selected]': {
    backgroundColor: 'var(--surface-gray-2, #f1f5f9)',
    color: 'var(--ink-gray-9, #0f172a)',
  },
  '.cm-completionDetail': { color: 'var(--ink-gray-5, #64748b)', fontStyle: 'italic' },
  '.cm-completionMatchedText': { color: 'var(--ink-blue-6, #2563eb)', textDecoration: 'none', fontWeight: '600' },
})

// CodeMirror's basicSetup falls back to a fixed, non-theme-aware highlight
// style (light-mode hex colors baked in, unchanged in dark mode). Registering
// our own - keyed to the same --ink-* tokens as the rest of the editor theme
// above - replaces that fallback and lets syntax colors flip with the app's
// theme instead of staying stuck in light-mode hues on a dark background.
const sqlHighlightStyle = HighlightStyle.define([
  { tag: [tags.keyword, tags.standard(tags.name)], color: 'var(--ink-blue-6, #2563eb)' },
  { tag: [tags.string, tags.special(tags.string)], color: 'var(--ink-green-6, #16a34a)' },
  { tag: [tags.lineComment, tags.blockComment], color: 'var(--ink-gray-5, #64748b)', fontStyle: 'italic' },
  { tag: [tags.number, tags.bool, tags.null], color: 'var(--ink-amber-6, #d97706)' },
  { tag: tags.typeName, color: 'var(--ink-amber-6, #d97706)' },
  { tag: tags.special(tags.name), color: 'var(--ink-violet-6, #7c3aed)' },
  { tag: tags.name, color: 'var(--ink-gray-8, #1e293b)' },
  { tag: [tags.operator, tags.punctuation, tags.paren, tags.brace, tags.squareBracket], color: 'var(--ink-gray-6, #5f6368)' },
])

const runKeymap = Prec.highest(keymap.of([{
  key: 'Ctrl-Enter',
  mac: 'Cmd-Enter',
  run: (v) => { emit('run', getSelectedOrAll(v)); return true },
}]))

const extensions = [
  theme,
  Prec.highest(syntaxHighlighting(sqlHighlightStyle)),
  lineNumbers(),
  drawSelection(),
  highlightActiveLine(),
  highlightActiveLineGutter(),
  history(),
  keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
  placeholder('SELECT name, creation FROM `tabUser` ORDER BY creation DESC LIMIT 5;'),
  autocompletion({ activateOnTyping: true, closeOnBlur: false, maxRenderedOptions: 10, icons: false }),
  sqlCompartment.of(sql({ dialect: cmDialect.value, upperCaseKeywords: true })),
  runKeymap,
]

defineExpose({ getQueryToRun })
</script>
