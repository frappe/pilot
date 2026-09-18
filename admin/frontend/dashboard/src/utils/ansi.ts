const ANSI_FG = {
  30: 'var(--ink-gray-5)',
  31: 'var(--ink-red-6)',
  32: 'var(--ink-green-6)',
  33: 'var(--ink-amber-6)',
  34: 'var(--ink-blue-6)',
  35: 'var(--ink-purple-6)',
  36: 'var(--ink-cyan-6)',
  37: 'var(--ink-gray-8)',
  // Bright maps to a stronger ink step, not a lighter one.
  90: 'var(--ink-gray-5)',
  91: 'var(--ink-red-7)',
  92: 'var(--ink-green-7)',
  93: 'var(--ink-amber-7)',
  94: 'var(--ink-blue-7)',
  95: 'var(--ink-purple-7)',
  96: 'var(--ink-cyan-7)',
  97: 'var(--ink-gray-9)',
}

export const escapeHtml = (text) => {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

const ansiToHtml = (text) => {
  let html = ''
  let openSpans = 0
  for (const part of text.split(/(\x1b\[[0-9;]*[A-Za-z])/)) {
    if (part.startsWith('\x1b[') && part.endsWith('m')) {
      for (const code of part.slice(2, -1).split(';')) {
        if (code === '0' || code === '') {
          html += '</span>'.repeat(openSpans)
          openSpans = 0
        } else if (code === '1') {
          html += '<span style="font-weight:bold">'
          openSpans++
        } else if (ANSI_FG[code]) {
          html += `<span style="color:${ANSI_FG[code]}">`
          openSpans++
        }
      }
    } else if (!part.startsWith('\x1b[')) {
      html += escapeHtml(part)
    }
  }
  return html + '</span>'.repeat(openSpans)
}

// Resolve \r (progress-bar overwrites): keep the last non-whitespace segment
const applyCarriageReturns = (raw) => {
  const parts = raw.split('\r')
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i].trim()) return parts[i].trimEnd()
  }
  return ''
}

export const processLine = (raw) => {
  return ansiToHtml(applyCarriageReturns(raw))
}
