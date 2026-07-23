export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

export function parseBranchVersion(branch) {
  if (!branch) return ''
  if (branch === 'develop') return 'Nightly'
  const match = /^version-(\d+)/.exec(branch)
  return match ? `Version ${match[1]}` : branch
}

export function toSentenceCase(text) {
  if (!text) return ''
  const spaced = text.replace(/[_-]+/g, ' ').trim()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase()
}
