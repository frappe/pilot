import * as monaco from 'monaco-editor'

/*
 * Espresso syntax palette for Monaco, replacing the stock vs/vs-dark themes.
 *
 * Dark is the espresso base16 scheme. Light mirrors the same hues at the darker
 * luminance frappe-ui uses for its ink tokens, so a file reads as the same
 * design in either theme. Editor chrome is pulled from the surface tokens in
 * index.css, so the editor and the panels around it share one background.
 */
const PALETTES = {
  dark: {
    background: '171717',
    foreground: 'd9d9d9',
    // base16 puts comments at #575757, which is only ~2.5:1 on this background.
    // The next step up in the ramp keeps them readable without shouting.
    comment: '7a7a7a',
    keyword: 'fa8a40',
    string: 'f4c25f',
    number: 'c993ef',
    type: '7dc5a2',
    function: '51decf',
    variable: '76bef9',
    special: 'ff7575',
    punctuation: '7a7a7a',
    lineNumber: '575757',
    lineNumberActive: 'afafaf',
    lineHighlight: '1f1f1f',
    selection: '383838',
    guide: '2e2e2e',
    widget: '1f1f1f',
    border: '383838',
  },
  light: {
    background: 'ffffff',
    foreground: '171717',
    comment: '6f6f6f',
    keyword: 'c2410c',
    string: 'a16207',
    number: '7e22ce',
    type: '15803d',
    function: '0f766e',
    variable: '1d4ed8',
    special: 'b91c1c',
    punctuation: '6f6f6f',
    lineNumber: '8f8f8f',
    lineNumberActive: '171717',
    lineHighlight: 'f7f7f7',
    selection: 'dbe9fb',
    guide: 'ececec',
    widget: 'ffffff',
    border: 'e2e2e2',
  },
}

// Monaco matches rules by dotted prefix, so "string" also covers
// "string.escape", "string.value.json" and friends.
function rulesFor(palette) {
  const roles = {
    comment: palette.comment,
    keyword: palette.keyword,
    string: palette.string,
    number: palette.number,
    type: palette.type,
    tag: palette.type,
    'attribute.name': palette.variable,
    'attribute.value': palette.string,
    function: palette.function,
    variable: palette.variable,
    identifier: palette.foreground,
    constant: palette.number,
    predefined: palette.number,
    regexp: palette.special,
    annotation: palette.special,
    metatag: palette.special,
    invalid: palette.special,
    namespace: palette.type,
    delimiter: palette.punctuation,
    operator: palette.punctuation,
    key: palette.variable,
  }
  const rules = [{ token: '', foreground: palette.foreground }]
  for (const [token, foreground] of Object.entries(roles)) rules.push({ token, foreground })
  rules.push({ token: 'comment', foreground: palette.comment, fontStyle: 'italic' })
  return rules
}

function themeFor(mode) {
  const palette = PALETTES[mode]
  const hex = (key) => `#${palette[key]}`
  return {
    base: mode === 'dark' ? 'vs-dark' : 'vs',
    // Own every token rather than inheriting a second, conflicting palette.
    inherit: false,
    rules: rulesFor(palette),
    colors: {
      'editor.background': hex('background'),
      'editor.foreground': hex('foreground'),
      'editorLineNumber.foreground': hex('lineNumber'),
      'editorLineNumber.activeForeground': hex('lineNumberActive'),
      'editor.lineHighlightBackground': hex('lineHighlight'),
      'editor.selectionBackground': hex('selection'),
      'editor.inactiveSelectionBackground': `${hex('selection')}80`,
      'editorCursor.foreground': hex('foreground'),
      'editorIndentGuide.background1': hex('guide'),
      'editorIndentGuide.activeBackground1': hex('border'),
      'editorWhitespace.foreground': hex('guide'),
      'editorWidget.background': hex('widget'),
      'editorWidget.border': hex('border'),
      'editorSuggestWidget.background': hex('widget'),
      'editorHoverWidget.background': hex('widget'),
      'editorHoverWidget.border': hex('border'),
      'editorGutter.background': hex('background'),
      'diffEditor.insertedTextBackground': '#7dc5a226',
      'diffEditor.removedTextBackground': '#ff757526',
      'diffEditor.insertedLineBackground': '#7dc5a214',
      'diffEditor.removedLineBackground': '#ff757514',
    },
  }
}

monaco.editor.defineTheme('espresso-light', themeFor('light'))
monaco.editor.defineTheme('espresso-dark', themeFor('dark'))

export function editorThemeName() {
  return document.documentElement.getAttribute('data-theme') === 'dark'
    ? 'espresso-dark'
    : 'espresso-light'
}

/** Point every Monaco surface, including colorize(), at the current app theme. */
export function applyEditorTheme() {
  monaco.editor.setTheme(editorThemeName())
}

applyEditorTheme()
