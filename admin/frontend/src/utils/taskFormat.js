export const STATUS_CONFIG = {
  success: { label: 'Success', theme: 'green', icon: 'lucide-check', iconBg: 'bg-surface-green-2 text-ink-green-8' },
  failed: { label: 'Failed', theme: 'red', icon: 'lucide-x', iconBg: 'bg-surface-red-2 text-ink-red-8' },
  running: {
    label: 'Running',
    theme: 'amber',
    icon: 'lucide-loader-circle animate-spin',
    iconBg: 'bg-surface-amber-2 text-ink-amber-8',
  },
  killed: { label: 'Killed', theme: 'gray', icon: 'lucide-square', iconBg: 'bg-surface-gray-2 text-ink-gray-6' },
}

export function statusConfig(task) {
  return STATUS_CONFIG[task.status] || STATUS_CONFIG.killed
}

const COMMAND_LABELS = {
  migrate: 'Migrate Site',
  'clear-cache': 'Clear Cache',
  'install-app': 'Install App',
  'uninstall-app': 'Uninstall App',
  'get-app': 'Get App',
  'remove-app': 'Remove App',
  'new-site': 'New Site',
  'drop-site': 'Drop Site',
  'backup-site': 'Backup Site',
  'delete-backup': 'Delete Backup',
  build: 'Build Bench',
  update: 'Update Bench',
  'get-and-install-app': 'Fetch & Install App',
  'add-and-install-app': 'Fetch & Install App on All Sites',
  'switch-branch': 'Switch Branch',
  'setup-nginx': 'Setup Nginx',
  'setup-production': 'Setup Production',
  'setup-letsencrypt': "Setup Let's Encrypt",
  'new-site-from-backup': 'Restore Site',
  'reinstall-site': 'Reinstall Site',
  'wizard-setup': 'Wizard Setup',
  'update-cli': 'Update CLI',
  'fetch-all-app-updates': 'Fetch App Updates',
}

export function commandLabel(command) {
  return COMMAND_LABELS[command] || command.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

const SITE_ARG_KEY = {
  migrate: 'site',
  'clear-cache': 'site',
  'install-app': 'site',
  'uninstall-app': 'site',
  'drop-site': 'site',
  'backup-site': 'site',
  'delete-backup': 'site',
  'get-and-install-app': 'site',
  'reinstall-site': 'site',
  'new-site': 'name',
  'new-site-from-backup': 'name',
}

export function siteLabel(task) {
  const key = SITE_ARG_KEY[task.command]
  return (key && task.args?.[key]) || 'Server-level'
}

export function relativeTime(iso) {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hr ago`
  return `${Math.floor(hours / 24)} d ago`
}

export function fmtDuration(seconds) {
  if (seconds == null) return ''
  const total = Math.round(seconds)
  if (total < 60) return `${total}s`
  return `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, '0')}s`
}

export function fmtDateTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
