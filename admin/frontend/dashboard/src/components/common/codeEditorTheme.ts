import { HighlightStyle } from '@codemirror/language'
import { EditorView } from '@codemirror/view'
import { tags } from '@lezer/highlight'

export const codeEditorTheme = EditorView.theme({
  // Not `&`: the tooltip container in document.body gets the theme class too, and would take the full page height.
  '&.cm-editor': {
    height: '100%',
    fontSize: '13px',
    color: 'var(--ink-gray-8, #1e293b)',
    backgroundColor: 'var(--surface-base, white)',
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-foldGutter .cm-gutterElement': { padding: '0 4px' },
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
  // The docs panel sits beside the list, so the list's tooltip must not clip it.
  '.cm-tooltip.cm-tooltip-autocomplete': { overflow: 'visible' },
  '.cm-tooltip-autocomplete > ul': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    fontSize: '12px',
  },
  '.cm-tooltip-autocomplete > ul > li': { color: 'var(--ink-gray-7, #475569)', padding: '3px 8px' },
  '.cm-tooltip-autocomplete > ul > li[aria-selected]': {
    backgroundColor: 'var(--surface-gray-2, #f1f5f9)',
    color: 'var(--ink-gray-9, #0f172a)',
  },
  '.cm-completionInfo': {
    whiteSpace: 'pre-wrap',
    maxWidth: '520px',
    maxHeight: '320px',
    overflowY: 'auto',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    fontSize: '12px',
    padding: '6px 8px',
  },
  '.cm-completionDetail': { color: 'var(--ink-gray-5, #64748b)', fontStyle: 'italic' },
  '.cm-completionMatchedText': {
    color: 'var(--ink-blue-5, #2563eb)',
    textDecoration: 'none',
    fontWeight: '600',
  },
})

// CodeMirror's basicSetup falls back to a fixed, non-theme-aware highlight
// style (light-mode hex colors baked in, unchanged in dark mode). Registering
// our own - keyed to the same --ink-* tokens as the rest of the editor theme
// above - replaces that fallback and lets syntax colors flip with the app's
// theme instead of staying stuck in light-mode hues on a dark background.
export const codeEditorHighlightStyle = HighlightStyle.define([
  { tag: [tags.keyword, tags.standard(tags.name)], color: 'var(--ink-blue-5, #2563eb)' },
  { tag: [tags.string, tags.special(tags.string)], color: 'var(--ink-green-5, #16a34a)' },
  {
    tag: [tags.lineComment, tags.blockComment],
    color: 'var(--ink-gray-5, #64748b)',
    fontStyle: 'italic',
  },
  { tag: [tags.number, tags.bool, tags.null], color: 'var(--ink-amber-5, #d97706)' },
  { tag: tags.typeName, color: 'var(--ink-amber-5, #d97706)' },
  { tag: tags.special(tags.name), color: 'var(--ink-violet-5, #7c3aed)' },
  { tag: tags.name, color: 'var(--ink-gray-8, #1e293b)' },
  {
    tag: [tags.operator, tags.punctuation, tags.paren, tags.brace, tags.squareBracket],
    color: 'var(--ink-gray-6, #5f6368)',
  },
])
