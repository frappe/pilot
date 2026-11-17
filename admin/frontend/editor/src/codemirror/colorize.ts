import type { Language } from '@codemirror/language'
import { highlightTree, tagHighlighter, tags } from '@lezer/highlight'

export const codeHighlighter = tagHighlighter([
  { tag: [tags.keyword, tags.modifier, tags.operatorKeyword], class: 'tok-keyword' },
  { tag: [tags.string, tags.special(tags.string), tags.attributeValue], class: 'tok-string' },
  { tag: [tags.number, tags.bool, tags.null, tags.atom, tags.self], class: 'tok-number' },
  { tag: [tags.typeName, tags.className, tags.namespace, tags.tagName], class: 'tok-type' },
  {
    tag: [
      tags.function(tags.variableName),
      tags.function(tags.propertyName),
      tags.function(tags.definition(tags.variableName)),
      tags.macroName,
    ],
    class: 'tok-function',
  },
  { tag: [tags.propertyName, tags.attributeName, tags.labelName], class: 'tok-variable' },
  { tag: [tags.regexp, tags.escape, tags.meta, tags.invalid], class: 'tok-special' },
  { tag: tags.comment, class: 'tok-comment' },
  { tag: [tags.punctuation, tags.operator], class: 'tok-punctuation' },
  { tag: tags.heading, class: 'tok-heading' },
  { tag: tags.emphasis, class: 'tok-emphasis' },
  { tag: tags.strong, class: 'tok-strong' },
  { tag: [tags.link, tags.url], class: 'tok-link' },
])

interface Token {
  from: number
  to: number
  classes: string
}

const entities: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }

const escapeHtml = (text: string) => text.replace(/[&<>"]/g, (char) => entities[char])

const charOffset = (text: string, byteOffset: number) => {
  const bytes = new TextEncoder().encode(text)

  if (bytes.length === text.length) return byteOffset

  return new TextDecoder().decode(bytes.slice(0, byteOffset)).length
}

const clamp = (value: number, max: number) => Math.min(Math.max(0, value), max)

const tokensFor = (line: string, language: Language | null) => {
  const tokens: Token[] = []

  if (language) {
    highlightTree(language.parser.parse(line), codeHighlighter, (from, to, classes) =>
      tokens.push({ from, to, classes }),
    )
  }

  return tokens
}

export const colorizeLine = (
  text: string,
  matchStart: number,
  matchEnd: number,
  language: Language | null,
) => {
  const raw = text.replace(/\r?\n$/, '').replaceAll('\t', ' ')
  const indent = raw.length - raw.trimStart().length
  const line = raw.slice(indent)
  const start = clamp(charOffset(raw, matchStart) - indent, line.length)
  const end = clamp(charOffset(raw, matchEnd) - indent, line.length)
  const tokens = tokensFor(line, language)
  const cuts = new Set([0, line.length, start, end, ...tokens.flatMap((t) => [t.from, t.to])])
  const points = [...cuts].sort((a, b) => a - b)
  const marked = start < end
  let html = ''

  for (let index = 0; index < points.length - 1; index++) {
    const from = points[index]
    const to = points[index + 1]
    const token = tokens.find((t) => t.from <= from && to <= t.to)
    const piece = escapeHtml(line.slice(from, to))

    if (marked && from === start) html += '<mark class="ed-mark">'

    html += token ? `<span class="${token.classes}">${piece}</span>` : piece

    if (marked && to === end) html += '</mark>'
  }

  return html
}
