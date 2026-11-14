import { HighlightStyle } from '@codemirror/language'
import { Prec } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { Tag, tags } from '@lezer/highlight'

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
    borderRadius: '8px',
    color: 'var(--ink-gray-8, #1e293b)',
    boxShadow: '0 12px 32px -8px rgba(0, 0, 0, 0.24)',
    overflow: 'hidden',
  },
  // The docs panel sits beside the list, so the list's tooltip must not clip it.
  '.cm-tooltip.cm-tooltip-autocomplete': { overflow: 'visible' },
  '.cm-tooltip.cm-tooltip-autocomplete > ul': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    fontSize: '12.5px',
    padding: '4px',
    minWidth: '300px',
    maxWidth: '520px',
    maxHeight: '288px',
  },
  '.cm-tooltip.cm-tooltip-autocomplete > ul > li': {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    height: '28px',
    padding: '0 10px 0 4px',
    borderRadius: '5px',
    color: 'var(--ink-gray-7, #475569)',
  },
  '.cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]': {
    backgroundColor: 'var(--surface-gray-3, #ededed)',
    color: 'var(--ink-gray-9, #0f172a)',
  },
  '.cm-kindIcon': {
    display: 'grid',
    placeItems: 'center',
    flexShrink: '0',
    width: '20px',
    height: '20px',
    borderRadius: '5px',
  },
  '.cm-completionLabel': { flexShrink: '0' },
  '.cm-completionDetail': {
    minWidth: '0',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    marginLeft: '0',
    color: 'var(--ink-gray-5, #64748b)',
    fontStyle: 'normal',
  },
  '.cm-kindLabel': {
    marginLeft: 'auto',
    paddingLeft: '16px',
    flexShrink: '0',
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
    fontSize: '12px',
    color: 'var(--ink-gray-5, #64748b)',
  },
  '.cm-completionMatchedText': {
    color: 'var(--ink-blue-5, #2563eb)',
    textDecoration: 'none',
    fontWeight: '600',
  },
  '.cm-tooltip.cm-completionInfo': {
    maxWidth: '460px',
    maxHeight: '320px',
    overflowY: 'auto',
    padding: '0',
    marginLeft: '4px',
  },
  '.cm-docs': { display: 'flex', flexDirection: 'column' },
  '.cm-docsSignature': {
    padding: '10px 12px',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    fontSize: '12.5px',
    lineHeight: '20px',
    color: 'var(--ink-gray-8, #1e293b)',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  '.cm-docsSignature + .cm-docsBody': { borderTop: '1px solid var(--outline-gray-2, #e8eaed)' },
  '.cm-docsBody': {
    padding: '10px 12px',
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
    fontSize: '13px',
    lineHeight: '1.5',
    color: 'var(--ink-gray-7, #475569)',
    whiteSpace: 'pre-wrap',
  },
  '.cm-docsBody strong': { color: 'var(--ink-gray-9, #0f172a)', fontWeight: '600' },
})

// CodeMirror's basicSetup falls back to a fixed, non-theme-aware highlight
// style (light-mode hex colors baked in, unchanged in dark mode). Registering
// our own - keyed to the same --ink-* tokens as the rest of the editor theme
// above - replaces that fallback and lets syntax colors flip with the app's
// theme instead of staying stuck in light-mode hues on a dark background.
export const codeTags = {
  repeat: Tag.define(tags.controlKeyword),
  exception: Tag.define(tags.controlKeyword),
  parameter: Tag.define(tags.variableName),
}

export const codeEditorHighlightStyle = HighlightStyle.define([
  { tag: [tags.keyword, tags.modifier], color: 'var(--ink-orange-7)' },
  { tag: tags.moduleKeyword, color: 'var(--ink-teal-7)' },
  { tag: codeTags.repeat, color: 'var(--ink-green-7)' },
  { tag: codeTags.exception, color: 'var(--ink-blue-7)' },
  {
    tag: [
      tags.function(tags.variableName),
      tags.function(tags.propertyName),
      tags.function(tags.definition(tags.variableName)),
    ],
    color: 'var(--ink-teal-7)',
  },
  { tag: [tags.propertyName, codeTags.parameter], color: 'var(--ink-blue-7)' },
  { tag: [tags.name, tags.variableName], color: 'var(--ink-gray-8)' },
  {
    tag: [tags.className, tags.typeName, tags.definition(tags.className), tags.meta],
    color: 'var(--ink-green-7)',
  },
  { tag: [tags.string, tags.special(tags.string)], color: 'var(--ink-amber-8)' },
  { tag: [tags.escape, tags.regexp], color: 'var(--ink-red-7)' },
  { tag: [tags.number, tags.bool, tags.null, tags.self], color: 'var(--ink-purple-7)' },
  { tag: [tags.lineComment, tags.blockComment], color: 'var(--ink-gray-5)' },
  { tag: tags.operator, color: 'var(--ink-gray-8)' },
  {
    tag: [
      tags.punctuation,
      tags.separator,
      tags.derefOperator,
      tags.paren,
      tags.brace,
      tags.squareBracket,
    ],
    color: 'var(--ink-gray-5)',
  },
])

export const browserFind = Prec.highest(
  EditorView.domEventHandlers({
    keydown: (event) => (event.ctrlKey || event.metaKey) && event.key === 'f',
  }),
)

export const consoleEditorTheme = EditorView.theme({
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
    backgroundColor: 'var(--surface-gray-2) !important',
  },
  '.cm-content ::selection, .cm-content::selection': {
    backgroundColor: 'var(--surface-gray-2) !important',
  },
  '&.cm-editor': { backgroundColor: 'var(--surface-gray-0)' },
  '.cm-gutters': { backgroundColor: 'transparent', border: 'none' },
  '.cm-lineNumbers .cm-gutterElement': { padding: '0 10px 0 16px', minWidth: '26px' },
  '.cm-foldGutter': { display: 'none !important' },
  '.cm-line': { paddingLeft: '6px' },
  '.cm-activeLineGutter': { backgroundColor: 'transparent', color: 'var(--ink-gray-8)' },
})
