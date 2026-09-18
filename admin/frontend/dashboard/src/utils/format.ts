export const formatBytes = (bytes) => {
  if(!Number.isFinite(bytes)) bytes = 0
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

export const formatMilliseconds = (ms) => {
  if (!Number.isFinite(ms)) return '—'
  if (ms < 1) return '<1 ms'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

export const formatCount = (value) => (Number.isFinite(value) ? value.toLocaleString() : '—')

export const parseBranchVersion = (branch) => {
  if (!branch) return ''
  if (branch === 'develop') return 'Nightly'
  const match = /^version-(\d+)/.exec(branch)
  return match ? `Version ${match[1]}` : branch
}

export const toSentenceCase = (text) => {
  if (!text) return ''
  const spaced = text.replace(/[_-]+/g, ' ').trim()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase()
}
