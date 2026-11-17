import { type EditorState, RangeSet, StateEffect, StateField, type Text } from '@codemirror/state'
import { Decoration, EditorView, GutterMarker, gutter, WidgetType } from '@codemirror/view'

import { relTime } from '@/utils'

export interface LineChanges {
  added?: number[]
  modified?: number[]
  deleted?: number[]
}

export interface BlameLine {
  author: string
  time: number
  summary: string
}

class ChangeMarker extends GutterMarker {
  constructor(readonly elementClass: string) {
    super()
  }
}

class BlameWidget extends WidgetType {
  constructor(readonly label: string) {
    super()
  }

  eq(other: BlameWidget) {
    return other.label === this.label
  }

  toDOM() {
    const span = document.createElement('span')

    span.className = 'cm-blame'
    span.textContent = this.label

    return span
  }
}

const markers = {
  added: new ChangeMarker('cm-gitAdded'),
  modified: new ChangeMarker('cm-gitModified'),
  deleted: new ChangeMarker('cm-gitDeleted'),
}

export const setLineChanges = StateEffect.define<LineChanges>()

export const setBlame = StateEffect.define<BlameLine[]>()

const changeMarkers = (doc: Text, changes: LineChanges) => {
  const kinds = Object.keys(markers) as (keyof typeof markers)[]

  const ranges = kinds.flatMap((kind) =>
    (changes[kind] ?? [])
      .map((line) => Math.max(1, line))
      .filter((line) => line <= doc.lines)
      .map((line) => markers[kind].range(doc.line(line).from)),
  )

  return RangeSet.of(ranges, true)
}

const changesField = StateField.define<RangeSet<GutterMarker>>({
  create: () => RangeSet.empty,
  update: (value, transaction) => {
    const effect = transaction.effects.findLast((effect) => effect.is(setLineChanges))

    if (effect) return changeMarkers(transaction.state.doc, effect.value)

    return value.map(transaction.changes)
  },
})

const blameLabel = ({ author, time, summary }: BlameLine) =>
  author === 'Uncommitted' ? 'Uncommitted changes' : `${author}, ${relTime(time)} • ${summary}`

const blameDecorations = (state: EditorState, lines: BlameLine[]) => {
  const line = state.doc.lineAt(state.selection.main.head)
  const blame = lines[line.number - 1]

  if (!blame) return Decoration.none

  const widget = Decoration.widget({ widget: new BlameWidget(blameLabel(blame)), side: 1 })

  return Decoration.set(widget.range(line.to))
}

export const blame = StateField.define({
  create: () => ({ lines: [] as BlameLine[], decorations: Decoration.none }),
  update: (value, transaction) => {
    const effect = transaction.effects.findLast((effect) => effect.is(setBlame))
    const lines = effect ? effect.value : value.lines

    if (!effect && !transaction.selection && !transaction.docChanged) return value

    return { lines, decorations: blameDecorations(transaction.state, lines) }
  },
  provide: (field) => EditorView.decorations.from(field, (value) => value.decorations),
})

export const gitGutter = [
  changesField,
  gutter({ class: 'cm-gitGutter', markers: (view) => view.state.field(changesField) }),
]
