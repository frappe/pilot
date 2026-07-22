// Parse a unified diff (`git diff` output) into a file header + hunks.
// Each hunk line keeps its raw text (including the leading +/-/space).
export function parseDiff(text) {
  const lines = (text || '').split('\n')
  if (lines.length && lines[lines.length - 1] === '') lines.pop()
  const headerLines = []
  let i = 0
  for (; i < lines.length; i++) {
    if (lines[i].startsWith('@@')) break
    headerLines.push(lines[i])
  }
  const header = headerLines.length ? headerLines.join('\n') + '\n' : ''
  const hunks = []
  let cur = null
  for (; i < lines.length; i++) {
    const ln = lines[i]
    if (ln.startsWith('@@')) {
      const m = ln.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)/)
      cur = {
        oldStart: m ? +m[1] : 0,
        newStart: m ? +m[3] : 0,
        context: m ? m[5] : '',
        lines: [],
      }
      hunks.push(cur)
    } else if (cur) {
      const c = ln[0]
      let type = ' '
      if (c === '+') type = '+'
      else if (c === '-') type = '-'
      else if (c === '\\') type = '\\'
      cur.lines.push({ type, text: ln })
    }
  }
  return { header, hunks }
}

// Build the body for one hunk keeping only the selected changed lines.
// reverse swaps the +/- roles, used when the patch will be `git apply --reverse`d
// (unstage / discard). Unselected changes become context (-) or are dropped (+).
function buildHunkBody(hunk, hi, selected, reverse) {
  const out = []
  let oldCount = 0
  let newCount = 0
  let changes = 0
  hunk.lines.forEach((ln, li) => {
    if (ln.type === '\\') {
      out.push(ln.text)
      return
    }
    if (ln.type === ' ') {
      out.push(ln.text)
      oldCount++
      newCount++
      return
    }
    const sel = selected.has(hi + ':' + li)
    const addType = reverse ? '-' : '+'
    const delType = reverse ? '+' : '-'
    if (ln.type === addType) {
      if (sel) {
        out.push(ln.text)
        newCount++
        changes++
      }
    } else if (ln.type === delType) {
      if (sel) {
        out.push(ln.text)
        oldCount++
        changes++
      } else {
        out.push(' ' + ln.text.slice(1))
        oldCount++
        newCount++
      }
    }
  })
  if (!changes) return null
  return (
    `@@ -${hunk.oldStart},${oldCount} +${hunk.newStart},${newCount} @@\n` +
    out.join('\n') +
    '\n'
  )
}

// Assemble an applyable patch from a parsed diff and a Set of selected line keys
// ("<hunkIndex>:<lineIndex>"). Returns null if nothing is selected.
export function buildPatch(parsed, selected, reverse) {
  let body = ''
  parsed.hunks.forEach((h, hi) => {
    const b = buildHunkBody(h, hi, selected, reverse)
    if (b) body += b
  })
  return body ? parsed.header + body : null
}

// All changed-line keys for a single hunk (used for whole-hunk actions).
export function hunkKeys(parsed, hi) {
  const set = new Set()
  parsed.hunks[hi].lines.forEach((ln, li) => {
    if (ln.type === '+' || ln.type === '-') set.add(hi + ':' + li)
  })
  return set
}
