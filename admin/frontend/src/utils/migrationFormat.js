export function kindLabel(kind) {
  return kind === 'update' ? 'App update' : 'Site migration'
}

export function siteSummary(op) {
  const names = (op.sites || []).map((s) => s.name)
  if (!names.length) return 'No sites'
  if (names.length <= 2) return names.join(', ')
  return `${names.slice(0, 2).join(', ')} +${names.length - 2}`
}

export function fmtDate(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

const STATE_TONE = {
  completed: 'green',
  reverted: 'blue',
  needs_attention: 'red',
  revert_failed: 'red',
  preparing: 'orange',
  updating: 'orange',
  migrating: 'orange',
  retrying: 'orange',
  reverting: 'orange',
}

const STATE_LABEL = {
  completed: 'Completed',
  reverted: 'Reverted',
  needs_attention: 'Needs attention',
  revert_failed: 'Revert failed',
  preparing: 'Preparing',
  updating: 'Updating',
  migrating: 'Migrating',
  retrying: 'Retrying',
  reverting: 'Reverting',
}

export function stateTone(state) {
  return STATE_TONE[state] || 'gray'
}

export function stateLabel(state) {
  return STATE_LABEL[state] || state
}
