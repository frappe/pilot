export function parseBranchVersion(branch) {
  if (!branch) return ''
  if (branch === 'develop') return 'Nightly'
  const match = /^version-(\d+)/.exec(branch)
  return match ? `Version ${match[1]}` : branch
}
