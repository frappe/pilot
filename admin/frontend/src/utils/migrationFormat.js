import { fmtDateTime } from './taskFormat.js'

export function kindLabel(kind) {
  return kind === 'update' ? 'App update' : 'Site migration'
}

export function opTitle(op) {
  if (op?.kind === 'site_migrate') return `Migrate ${op.sites?.[0]?.name || 'site'}`
  // Updates all look the same; the run time is what tells them apart.
  return fmtDateTime(op?.started_at || op?.created_at)
}

export function patchSkipped(op) {
  const patch = op?.diagnosis?.patch
  if (!patch) return false
  return (op.decisions || []).some(
    (decision) =>
      decision.action === 'bypass_patch' &&
      decision.patch === patch &&
      decision.site === op.failed_site,
  )
}

export function appsSummary(op) {
  const names = (op.apps || []).map((a) => a.name)
  if (!names.length) return ''
  if (names.length <= 2) return names.join(', ')
  return `${names.slice(0, 2).join(', ')} +${names.length - 2}`
}

const STATE_TONE = {
  completed: 'green',
  reverted: 'blue',
  needs_attention: 'red',
  revert_failed: 'red',
  preparing: 'orange',
  backing_up: 'orange',
  updating: 'orange',
  migrating: 'orange',
  retrying: 'orange',
  reverting_apps: 'orange',
  reverting_sites: 'orange',
  restarting: 'orange',
}

const STATE_LABEL = {
  completed: 'Completed',
  reverted: 'Reverted',
  needs_attention: 'Needs attention',
  revert_failed: 'Revert failed',
  preparing: 'Preparing',
  backing_up: 'Backing up',
  updating: 'Updating',
  migrating: 'Migrating',
  retrying: 'Retrying',
  reverting_apps: 'Reverting apps',
  reverting_sites: 'Recovering sites',
  restarting: 'Restarting services',
}

const STATE_ICON = {
  green: { icon: 'lucide-check', iconBg: 'bg-surface-green-2 text-ink-green-8' },
  red: { icon: 'lucide-x', iconBg: 'bg-surface-red-2 text-ink-red-8' },
  blue: { icon: 'lucide-rotate-ccw', iconBg: 'bg-surface-blue-2 text-ink-blue-8' },
  orange: {
    icon: 'lucide-loader-circle animate-spin',
    iconBg: 'bg-surface-amber-2 text-ink-amber-8',
  },
  gray: { icon: 'lucide-clock-3', iconBg: 'bg-surface-gray-3 text-ink-gray-6' },
}

export function stateIcon(state) {
  return STATE_ICON[stateTone(state)]
}

export function stateTone(state) {
  return STATE_TONE[state] || 'gray'
}

export function stateLabel(state) {
  return STATE_LABEL[state] || state
}

// Per-site lifecycle: pending -> backing up -> running -> success / failed / recovered
export function siteStatus(site) {
  if (site.migration_status === 'recovering')
    return { label: 'Recovering', tone: 'orange', busy: true, value: 'recovering' }
  if (site.migration_status === 'recovered')
    return { label: 'Recovered', tone: 'green', value: 'recovered' }
  if (site.migration_status === 'success')
    return { label: 'Success', tone: 'green', value: 'success' }
  if (site.migration_status === 'running')
    return { label: 'Running', tone: 'orange', busy: true, value: 'running' }
  if (site.migration_status === 'failed') return { label: 'Failed', tone: 'red', value: 'failed' }
  if (site.backup_status === 'backing_up')
    return { label: 'Backing up', tone: 'orange', busy: true, value: 'backing_up' }
  if (site.backup_status === 'failed') return { label: 'Failed', tone: 'red', value: 'failed' }
  if (site.backup_status === 'backed_up')
    return { label: 'Backed up', tone: 'blue', value: 'backed_up' }
  if (site.backup_status === 'unsupported')
    return { label: 'Backup skipped', tone: 'gray', value: 'unsupported' }
  return { label: 'Pending', tone: 'gray', value: 'pending' }
}
