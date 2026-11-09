<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import { Codemirror } from 'vue-codemirror'
import type { SQLDialect } from '@codemirror/lang-sql'
import { MariaSQL, PostgreSQL, SQLite as SQLiteDialect, sql } from '@codemirror/lang-sql'
import type { Completion } from '@codemirror/autocomplete'
import { autocompletion } from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'

import {
  keymap,
  EditorView,
  lineNumbers,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  placeholder,
} from '@codemirror/view'
import { Compartment, Prec } from '@codemirror/state'
import { syntaxHighlighting } from '@codemirror/language'

import { codeEditorHighlightStyle, codeEditorTheme } from '@/components/common/codeEditorTheme'
import type { TableColumn, TableSchema } from '@/types/database'

interface Props {
  modelValue?: string
  schema?: TableSchema[]
  dbType?: string
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  schema: () => [],
  dbType: 'mariadb',
})

const emit = defineEmits(['update:modelValue', 'run'])

const model = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const view = shallowRef<EditorView | null>(null)

const onReady = ({ view: v }: { view: EditorView }) => {
  view.value = v
}

const cmSchema = computed(() => {
  const s: Record<string, Completion[]> = {}
  for (const table of props.schema) {
    s[table.name] = table.columns.map((c: TableColumn) => ({
      label: c.name,
      type: 'property',
      detail: c.type,
    }))
  }
  return s
})

const dialects: Record<string, SQLDialect> = {
  mariadb: MariaSQL,
  postgres: PostgreSQL,
  sqlite: SQLiteDialect,
}

const cmDialect = computed(() => dialects[props.dbType] || MariaSQL)

const sqlCompartment = new Compartment()

const reconfigureSql = () => {
  if (!view.value) return
  view.value.dispatch({
    effects: sqlCompartment.reconfigure(
      sql({ dialect: cmDialect.value, upperCaseKeywords: true, schema: cmSchema.value }),
    ),
  })
}

watch([cmSchema, cmDialect], reconfigureSql)

const getSelectedOrAll = (v: EditorView) => {
  const { from, to } = v.state.selection.main
  return from !== to ? v.state.sliceDoc(from, to) : v.state.doc.toString()
}

const getQueryToRun = () => {
  return view.value ? getSelectedOrAll(view.value) : props.modelValue
}

const runKeymap = Prec.highest(
  keymap.of([
    {
      key: 'Ctrl-Enter',
      mac: 'Cmd-Enter',
      run: (v) => {
        emit('run', getSelectedOrAll(v))
        return true
      },
    },
  ]),
)

const extensions = [
  codeEditorTheme,
  Prec.highest(syntaxHighlighting(codeEditorHighlightStyle)),
  lineNumbers(),
  drawSelection(),
  highlightActiveLine(),
  highlightActiveLineGutter(),
  history(),
  keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
  placeholder('SELECT name, creation FROM `tabUser` ORDER BY creation DESC LIMIT 5;'),
  autocompletion({
    activateOnTyping: true,
    closeOnBlur: false,
    maxRenderedOptions: 10,
    icons: false,
  }),
  sqlCompartment.of(sql({ dialect: cmDialect.value, upperCaseKeywords: true })),
  runKeymap,
]

defineExpose({ getQueryToRun })
</script>

<template>
  <Codemirror
    v-model="model"
    :extensions="extensions"
    :autofocus="true"
    :style="{ height: '100%' }"
    @ready="onReady"
  />
</template>
