import { type EditorState, type Range, RangeSet, StateField, type Text } from '@codemirror/state'
import {
  Decoration,
  type DecorationSet,
  EditorView,
  GutterMarker,
  gutterLineClass,
  WidgetType,
} from '@codemirror/view'

interface Conflict {
  start: number
  separator: number
  end: number
  current: string
  incoming: string
}

type Resolution = 'current' | 'incoming' | 'both'

const resolutions: [Resolution, string][] = [
  ['current', 'Accept Current'],
  ['incoming', 'Accept Incoming'],
  ['both', 'Accept Both'],
]

const startMarker = /^<{7}/
const baseMarker = /^\|{7}/
const separatorMarker = /^={7}$/
const endMarker = /^>{7}/

const parseConflicts = (doc: Text) => {
  const conflicts: Conflict[] = []
  let open: { start: number; separator: number; base: number } | null = null

  for (let number = 1; number <= doc.lines; number++) {
    const text = doc.line(number).text

    if (startMarker.test(text)) open = { start: number, separator: 0, base: 0 }
    else if (!open) continue
    else if (baseMarker.test(text)) open.base = number
    else if (separatorMarker.test(text)) open.separator = number
    else if (endMarker.test(text) && open.separator) {
      const { start, separator, base } = open
      const lines = (from: number, to: number) =>
        from > to ? '' : doc.sliceString(doc.line(from).from, doc.line(to).to)

      conflicts.push({
        start,
        separator,
        end: number,
        current: lines(start + 1, (base || separator) - 1),
        incoming: lines(separator + 1, number - 1),
      })
      open = null
    }
  }

  return conflicts
}

const resolvedText = (conflict: Conflict, resolution: Resolution) => {
  if (resolution === 'current') return conflict.current

  if (resolution === 'incoming') return conflict.incoming

  return `${conflict.current}\n${conflict.incoming}`
}

const resolve = (view: EditorView, start: number, resolution: Resolution) => {
  const conflict = view.state.field(mergeConflicts).conflicts.find((c) => c.start === start)

  if (!conflict) return

  const { doc } = view.state

  view.dispatch({
    changes: {
      from: doc.line(conflict.start).from,
      to: doc.line(conflict.end).to,
      insert: resolvedText(conflict, resolution),
    },
  })
}

class ConflictActions extends WidgetType {
  constructor(readonly start: number) {
    super()
  }

  eq(other: ConflictActions) {
    return other.start === this.start
  }

  toDOM(view: EditorView) {
    const bar = document.createElement('div')

    bar.className = 'cm-conflictActions'
    bar.append(
      ...resolutions.map(([resolution, label]) => {
        const button = document.createElement('button')

        button.type = 'button'
        button.textContent = label
        button.addEventListener('click', () => resolve(view, this.start, resolution))

        return button
      }),
    )

    return bar
  }
}

const startLineMarker = new (class extends GutterMarker {
  elementClass = 'cm-conflictStart'
})()

const currentLine = Decoration.line({ class: 'cm-conflictCurrent' })
const incomingLine = Decoration.line({ class: 'cm-conflictIncoming' })

const conflictDecorations = (doc: Text, conflicts: Conflict[]) => {
  const ranges: Range<Decoration>[] = []

  for (const conflict of conflicts) {
    const actions = new ConflictActions(conflict.start)

    ranges.push(
      Decoration.widget({ widget: actions, block: true, side: -1 }).range(
        doc.line(conflict.start).from,
      ),
    )

    for (let number = conflict.start; number <= conflict.end; number++) {
      const decoration = number <= conflict.separator ? currentLine : incomingLine

      ranges.push(decoration.range(doc.line(number).from))
    }
  }

  return Decoration.set(ranges, true)
}

const conflictState = (doc: Text) => {
  const conflicts = parseConflicts(doc)
  const starts = conflicts.map((conflict) => startLineMarker.range(doc.line(conflict.start).from))

  return {
    conflicts,
    decorations: conflictDecorations(doc, conflicts),
    gutter: RangeSet.of<GutterMarker>(starts),
  }
}

export const mergeConflicts = StateField.define<{
  conflicts: Conflict[]
  decorations: DecorationSet
  gutter: RangeSet<GutterMarker>
}>({
  create: (state) => conflictState(state.doc),
  update: (value, transaction) =>
    transaction.docChanged ? conflictState(transaction.state.doc) : value,
  provide: (field) => [
    EditorView.decorations.from(field, (value) => value.decorations),
    gutterLineClass.from(field, (value) => value.gutter),
  ],
})

export const conflictCount = (state: EditorState) =>
  state.field(mergeConflicts, false)?.conflicts.length ?? 0
