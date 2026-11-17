import { closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import {
  bracketMatching,
  foldKeymap,
  indentOnInput,
  indentUnit,
  syntaxHighlighting,
  syntaxTree,
} from '@codemirror/language'
import { type Diagnostic, linter } from '@codemirror/lint'
import { highlightSelectionMatches, searchKeymap } from '@codemirror/search'
import { EditorState } from '@codemirror/state'
import {
  crosshairCursor,
  drawSelection,
  dropCursor,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  rectangularSelection,
} from '@codemirror/view'

import { codeHighlighter } from '@/codemirror/colorize'

const editorTheme = EditorView.theme({
  '&.cm-editor': {
    height: '100%',
    fontSize: 'var(--editor-font-size, 13px)',
    color: 'var(--code-fg)',
    backgroundColor: 'var(--code-bg)',
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    lineHeight: '1.6',
  },
  '.cm-content': { caretColor: 'var(--code-fg)' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--code-fg)' },
  '.cm-gutters': {
    backgroundColor: 'var(--code-bg)',
    color: 'var(--code-line-number)',
    border: 'none',
  },
  '.cm-lineNumbers .cm-gutterElement': { padding: '0 8px 0 16px' },
  '.cm-activeLine': { backgroundColor: 'var(--code-line-highlight)' },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
    color: 'var(--code-line-number-active)',
  },
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection':
    { backgroundColor: 'var(--code-selection)' },
  '.cm-selectionMatch': { backgroundColor: 'var(--code-guide)' },
  '&.cm-focused .cm-matchingBracket': {
    backgroundColor: 'transparent',
    outline: '1px solid var(--code-border)',
  },
  '.cm-searchMatch': { backgroundColor: 'var(--code-search)' },
  '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: 'var(--code-search-active)' },
  '.cm-foldPlaceholder': {
    backgroundColor: 'var(--code-guide)',
    border: 'none',
    color: 'var(--code-comment)',
  },
  '.cm-panels': {
    backgroundColor: 'var(--surface-chrome)',
    color: 'var(--code-fg)',
  },
  '.cm-panels.cm-panels-top': { borderBottom: '1px solid var(--code-border)' },
  '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--code-border)' },
  '.cm-textfield, .cm-button': {
    backgroundColor: 'var(--code-bg)',
    backgroundImage: 'none',
    border: '1px solid var(--code-border)',
    borderRadius: '6px',
    color: 'var(--code-fg)',
  },
  '.cm-tooltip': {
    backgroundColor: 'var(--code-widget)',
    border: '1px solid var(--code-border)',
    borderRadius: '8px',
    color: 'var(--code-fg)',
    overflow: 'hidden',
  },
  '.cm-tooltip-autocomplete ul li[aria-selected]': {
    backgroundColor: 'var(--code-selection)',
    color: 'var(--code-fg)',
  },
  '&.cm-merge-b .cm-changedLine, .cm-inlineChangedLine': {
    backgroundColor: 'var(--code-inserted-line)',
  },
  '&.cm-merge-b .cm-changedText': { background: 'var(--code-inserted-text)' },
  '&.cm-merge-a .cm-changedLine, .cm-deletedChunk': {
    backgroundColor: 'var(--code-removed-line)',
  },
  '&.cm-merge-a .cm-changedText, .cm-deletedChunk .cm-deletedText': {
    background: 'var(--code-removed-text)',
  },
  '.cm-changeGutter': { width: '3px', paddingLeft: '0' },
  '&.cm-merge-b .cm-changedLineGutter, .cm-inlineChangedLineGutter': {
    background: 'var(--git-added)',
  },
  '&.cm-merge-a .cm-changedLineGutter, .cm-deletedLineGutter': {
    background: 'var(--git-deleted)',
  },
})

const shared = [
  highlightSpecialChars(),
  EditorState.tabSize.of(2),
  indentUnit.of('  '),
  syntaxHighlighting(codeHighlighter),
  editorTheme,
]

export const syntaxErrors = linter((view) => {
  const diagnostics: Diagnostic[] = []
  const end = view.state.doc.length

  syntaxTree(view.state).iterate({
    enter: (node) => {
      if (!node.type.isError) return

      diagnostics.push({
        from: Math.min(node.from, end),
        to: Math.min(Math.max(node.to, node.from + 1), end),
        severity: 'error',
        message: 'Syntax error',
      })
    },
  })

  return diagnostics
})

export const editorSetup = [
  ...shared,
  highlightActiveLineGutter(),
  history(),
  drawSelection(),
  dropCursor(),
  EditorState.allowMultipleSelections.of(true),
  indentOnInput(),
  bracketMatching(),
  closeBrackets(),
  rectangularSelection(),
  crosshairCursor(),
  highlightActiveLine(),
  highlightSelectionMatches(),
  keymap.of([
    ...closeBracketsKeymap,
    ...defaultKeymap,
    ...searchKeymap,
    ...historyKeymap,
    ...foldKeymap,
    ...completionKeymap,
    indentWithTab,
  ]),
]

export const viewerSetup = [
  ...shared,
  EditorState.readOnly.of(true),
  EditorView.contentAttributes.of({ inputmode: 'none' }),
  keymap.of(searchKeymap),
]
