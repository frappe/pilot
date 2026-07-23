import { monaco } from '@/monaco'

const CONFLICT_START = /^<{7}\s*(.*)$/m
const CONFLICT_SEP = /^={7}$/m
const CONFLICT_END = /^>{7}\s*(.*)$/m

export function useMergeConflicts(editor, model, onChange, onResolve, opts = {}) {
  const dark = !!opts.isDark
  const decos = editor?.createDecorationsCollection()
  let resolving = false
  let contentListener = null
  let conflicts = []
  let zoneIds = []

  function parseConflicts(content) {
    const normalized = content.replace(/\r\n/g, '\n')
    const lines = normalized.split('\n')
    const result = []
    let i = 0
    while (i < lines.length) {
      if (CONFLICT_START.test(lines[i])) {
        const startLine = i + 1
        let currentLines = []
        let incomingLines = []
        let mode = 'current'
        let endLine = startLine
        i++
        while (i < lines.length) {
          if (CONFLICT_SEP.test(lines[i])) {
            mode = 'incoming'
            i++
            continue
          }
          if (CONFLICT_END.test(lines[i])) {
            endLine = i + 1
            i++
            break
          }
          if (mode === 'current') currentLines.push(lines[i])
          else incomingLines.push(lines[i])
          i++
        }
        result.push({
          startLine,
          endLine,
          current: currentLines.join('\n'),
          incoming: incomingLines.join('\n'),
          currentCount: currentLines.length,
        })
      } else {
        i++
      }
    }
    return result
  }

  function buildZoneDom(conflict) {
    const wrap = document.createElement('div')
    wrap.className = 'mc-zone ' + (dark ? 'mc-dark' : 'mc-light')

    const mk = (text, kind) => {
      const b = document.createElement('button')
      b.type = 'button'
      b.className = 'mc-btn'
      b.textContent = text
      b.addEventListener('click', (e) => {
        e.preventDefault()
        e.stopPropagation()
        resolveOne(conflict.startLine, kind)
      })
      return b
    }

    wrap.append(
      mk('Accept Current', 'current'),
      mk('Accept Incoming', 'incoming'),
      mk('Accept Both', 'both'),
    )
    return wrap
  }

  function clearZones() {
    if (!editor || !zoneIds.length) return
    editor.changeViewZones((acc) => {
      for (const id of zoneIds) acc.removeZone(id)
    })
    zoneIds = []
  }

  function render() {
    if (!editor || !model || resolving) return
    const content = model.getValue()
    conflicts = parseConflicts(content)
    onChange?.(conflicts)

    clearZones()

    if (!conflicts.length) {
      decos.clear()
      return
    }

    const decorations = []
    for (const c of conflicts) {
      const sepLine = c.startLine + c.currentCount

      decorations.push({
        range: new monaco.Range(c.startLine, 1, sepLine, 1),
        options: { className: 'mc-current-region', isWholeLine: true },
      })
      decorations.push({
        range: new monaco.Range(sepLine + 1, 1, c.endLine, 1),
        options: { className: 'mc-incoming-region', isWholeLine: true },
      })
      decorations.push({
        range: new monaco.Range(c.startLine, 1, c.startLine, 1),
        options: { linesDecorationsClassName: 'mc-gutter-marker' },
      })
    }
    decos.set(decorations)

    editor.changeViewZones((acc) => {
      for (const c of conflicts) {
        const id = acc.addZone({
          afterLineNumber: Math.max(0, c.startLine - 1),
          heightInPx: 26,
          domNode: buildZoneDom(c),
        })
        zoneIds.push(id)
      }
    })
  }

  function replaceConflict(conflict, text) {
    resolving = true
    const range = new monaco.Range(
      conflict.startLine,
      1,
      conflict.endLine,
      model.getLineMaxColumn(conflict.endLine),
    )
    editor.executeEdits('merge-resolve', [{ range, text, forceMoveMarkers: true }])
    resolving = false
  }

  function resolveOne(startLine, kind) {
    const cs = parseConflicts(model.getValue())
    const c = cs.find((x) => x.startLine === startLine)
    if (!c) return
    const text =
      kind === 'current' ? c.current : kind === 'incoming' ? c.incoming : c.current + '\n' + c.incoming
    replaceConflict(c, text)
    onResolve?.(parseConflicts(model.getValue()))
    render()
  }

  function acceptAllCurrent() {
    for (const c of [...conflicts].reverse()) replaceConflict(c, c.current)
    onResolve?.(conflicts)
    render()
  }

  function acceptAllIncoming() {
    for (const c of [...conflicts].reverse()) replaceConflict(c, c.incoming)
    onResolve?.(conflicts)
    render()
  }

  function acceptAllBoth() {
    for (const c of [...conflicts].reverse()) replaceConflict(c, c.current + '\n' + c.incoming)
    onResolve?.(conflicts)
    render()
  }

  if (model) {
    contentListener = model.onDidChangeContent(() => render())
    render()
  }

  function dispose() {
    decos?.clear()
    clearZones()
    contentListener?.dispose()
  }

  return {
    acceptAllCurrent,
    acceptAllIncoming,
    acceptAllBoth,
    dispose,
  }
}
