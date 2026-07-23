export function baseName(p) {
  const i = p.lastIndexOf('/')
  return i === -1 ? p : p.slice(i + 1)
}

export function dirName(p) {
  const i = p.lastIndexOf('/')
  return i === -1 ? '' : p.slice(0, i)
}

export function relTime(sec) {
  const d = Math.max(0, Math.floor(Date.now() / 1000) - sec)
  if (d < 10) return 'just now'
  if (d < 60) return d + 's ago'
  if (d < 3600) return Math.floor(d / 60) + 'm ago'
  if (d < 86400) return Math.floor(d / 3600) + 'h ago'
  if (d < 604800) return Math.floor(d / 86400) + 'd ago'
  if (d < 2592000) return Math.round(d / 604800) + 'w ago'
  if (d < 31536000) return Math.round(d / 2592000) + 'mo ago'
  return Math.round(d / 31536000) + 'y ago'
}
