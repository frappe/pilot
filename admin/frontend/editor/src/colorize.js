import { monaco } from '@/monaco'

// Private-use sentinels: colorize() escapes &<> but leaves these alone, so the
// match range survives tokenization and can be swapped for a <mark> afterwards.
const MARK_OPEN = '\uE000'
const MARK_CLOSE = '\uE001'
const MARK_HTML = '<mark class="rounded-sm bg-surface-amber-2 text-ink-gray-9">'

const loaders = new Map()

// colorize() can resolve before a lazily registered language's tokenizer exists,
// which silently yields unhighlighted HTML. Run the loader once per language.
function loadLanguage(language) {
  if (!loaders.has(language)) {
    const entry = monaco.languages.getLanguages().find((l) => l.id === language)
    loaders.set(language, Promise.resolve(entry?.loader ? entry.loader() : null))
  }
  return loaders.get(language)
}

// ripgrep reports match offsets in bytes; the DOM works in UTF-16 code units.
function charOffset(text, byteOffset) {
  const bytes = new TextEncoder().encode(text)
  if (bytes.length === text.length) return byteOffset
  return new TextDecoder().decode(bytes.slice(0, byteOffset)).length
}

/** Syntax-highlighted HTML for one line, with an optional match range marked. */
export async function colorizeLine(text, language, match) {
  const line = text.replace(/\r?\n$/, '')
  let source = line
  if (match && match.end > match.start) {
    const start = charOffset(line, match.start)
    const end = charOffset(line, match.end)
    source = line.slice(0, start) + MARK_OPEN + line.slice(start, end) + MARK_CLOSE + line.slice(end)
  }
  await loadLanguage(language)
  const html = await monaco.editor.colorize(source, language, { tabSize: 4 })
  return html
    .replace(/<br\/?>$/, '')
    .replaceAll(MARK_OPEN, MARK_HTML)
    .replaceAll(MARK_CLOSE, '</mark>')
}
