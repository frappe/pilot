import type { Completion } from '@codemirror/autocomplete'
import type { EditorView, Rect } from '@codemirror/view'

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

const positionInfo = (_view: EditorView, list: Rect, _option: Rect, info: Rect, space: Rect) => {
  const width = info.right - info.left
  const isLeft =
    space.right - list.right < width && list.left - space.left > space.right - list.right

  return { style: 'top: 0', class: isLeft ? 'cm-completionInfo-left' : 'cm-completionInfo-right' }
}

export const completionKinds = {
  icons: false,
  positionInfo,
  addToOptions: [
    { render: renderIcon, position: 20 },
    { render: renderKind, position: 90 },
  ],
}

export const completionDetail = (detail: string | undefined) =>
  detail && !rawTypes.has(detail) ? detail : undefined

export const docstringNode = (docstring: string) => {
  const lines = docstring.trim().split('\n')
  const gap = lines.findIndex((line) => line.trim() === '')
  const head = gap === -1 ? [] : lines.slice(0, gap)
  const hasSignature = head.length > 0 && head.every((line) => line.includes('('))
  const body = (hasSignature ? lines.slice(gap) : lines).join('\n').trim()
  const root = element('div', 'cm-docs')

  if (hasSignature) root.append(element('div', 'cm-docsSignature', head.join('\n')))

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
