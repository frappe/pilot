import type { Completion } from '@codemirror/autocomplete'

type Kind = { label: string; icon: string; tone: string }

const kinds: Record<string, Kind> = {
  method: {
    label: 'Function',
    icon: 'lucide-square-function',
    tone: 'bg-surface-violet-2 text-ink-violet-6',
  },
  function: {
    label: 'Function',
    icon: 'lucide-square-function',
    tone: 'bg-surface-violet-2 text-ink-violet-6',
  },
  class: { label: 'Class', icon: 'lucide-box', tone: 'bg-surface-amber-2 text-ink-amber-6' },
  namespace: { label: 'Module', icon: 'lucide-braces', tone: 'bg-surface-blue-2 text-ink-blue-6' },
  property: { label: 'Field', icon: 'lucide-tag', tone: 'bg-surface-green-2 text-ink-green-6' },
  variable: {
    label: 'Variable',
    icon: 'lucide-variable',
    tone: 'bg-surface-cyan-2 text-ink-cyan-6',
  },
  keyword: {
    label: 'Keyword',
    icon: 'lucide-key-round',
    tone: 'bg-surface-gray-3 text-ink-gray-6',
  },
  table: { label: 'Table', icon: 'lucide-table', tone: 'bg-surface-amber-2 text-ink-amber-6' },
  column: {
    label: 'Column',
    icon: 'lucide-columns-3',
    tone: 'bg-surface-green-2 text-ink-green-6',
  },
}

const fallback: Kind = {
  label: 'Text',
  icon: 'lucide-type',
  tone: 'bg-surface-gray-3 text-ink-gray-6',
}
const rawTypes = new Set([
  'function',
  'module',
  'instance',
  'class',
  'keyword',
  'statement',
  'param',
  'property',
  'path',
])

const kindOf = (completion: Completion) => kinds[completion.type ?? ''] ?? fallback

const element = (tag: string, className: string, text = '') => {
  const node = document.createElement(tag)

  node.className = className
  node.textContent = text

  return node
}

const renderIcon = (completion: Completion) => {
  const kind = kindOf(completion)
  const tile = element('span', `cm-kindIcon ${kind.tone}`)

  tile.append(element('span', `${kind.icon} size-3`))

  return tile
}

const renderKind = (completion: Completion) =>
  element('span', 'cm-kindLabel', kindOf(completion).label)

export const completionKinds = {
  icons: false,
  addToOptions: [
    { render: renderIcon, position: 20 },
    { render: renderKind, position: 90 },
  ],
}

export const completionDetail = (detail: string | undefined) =>
  detail && !rawTypes.has(detail) ? detail : undefined

export const docstringNode = (docstring: string) => {
  const [first, ...rest] = docstring.trim().split('\n')
  const hasSignature = first.includes('(') && (rest[0] ?? '').trim() === ''
  const body = (hasSignature ? rest : [first, ...rest]).join('\n').trim()
  const root = element('div', 'cm-docs')

  if (hasSignature) root.append(element('div', 'cm-docsSignature', first))

  if (body) {
    const text = element('div', 'cm-docsBody')

    body.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
      text.append(
        part.startsWith('**')
          ? element('strong', '', part.slice(2, -2))
          : document.createTextNode(part),
      )
    })

    root.append(text)
  }

  return root
}
