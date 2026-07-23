import { monaco } from '@/monaco'

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

/**
 * Syntax-highlighted HTML for a single result line, trimmed of its indent so
 * narrow panels show the code rather than the whitespace.
 *
 * Returns the match range rebased onto the trimmed line, for markMatch().
 */
export async function colorizeLine(text, language, match) {
  // One space per tab keeps the string length stable, so the offsets below stay
  // valid; colorize() would otherwise expand tabs and shift them.
  const raw = text.replace(/\r?\n$/, '').replaceAll('\t', ' ')
  const indent = raw.length - raw.trimStart().length
  const line = raw.slice(indent)
  await loadLanguage(language)
  const html = (await monaco.editor.colorize(line, language, { tabSize: 1 })).replace(/<br\/?>$/, '')
  return {
    html,
    start: Math.max(0, charOffset(raw, match.start) - indent),
    end: Math.max(0, charOffset(raw, match.end) - indent),
  }
}

/** Wrap a character range of already-rendered markup in a <mark>. */
export function markMatch(root, start, end) {
  if (!root || end <= start) return
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const range = document.createRange()
  let offset = 0
  let started = false
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const length = node.textContent.length
    if (!started && start < offset + length) {
      range.setStart(node, start - offset)
      started = true
    }
    if (started && end <= offset + length) {
      range.setEnd(node, end - offset)
      const mark = document.createElement('mark')
      mark.className = 'ed-mark'
      // extractContents, not surroundContents: a match usually spans several
      // token spans, which surroundContents refuses to wrap.
      mark.appendChild(range.extractContents())
      range.insertNode(mark)
      return
    }
    offset += length
  }
}
