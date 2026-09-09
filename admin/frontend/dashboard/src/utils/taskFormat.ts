import { relativeTime } from './time.ts'

const STATUS_CONFIG = {
  queued: {
    label: 'Queued',
    theme: 'blue',
  },
  success: {
    label: 'Success',
    theme: 'green',
  },
  failed: {
    label: 'Failed',
    theme: 'red',
  },
  running: {
    label: 'Running',
    theme: 'amber',
  },
  killed: {
    label: 'Killed',
    theme: 'gray',
  },
}

export const statusConfig = (task) => {
  return STATUS_CONFIG[task.status] || STATUS_CONFIG.killed
}

export const isTaskActive = (task) => {
  return task?.status === 'queued' || task?.status === 'running'
}

// The backend decides this - some tasks leave partial state behind when killed.
export const isTaskCancellable = (task) => {
  return Boolean(task?.is_cancellable)
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
  'setup-letsencrypt': "Setup Let's Encrypt",
  'new-site-from-backup': 'Restore Site',
  'rename-site': 'Rename Site',
  'reinstall-site': 'Reinstall Site',
  'wizard-setup': 'Wizard Setup',
  'update-cli': 'Update CLI',
  'fetch-all-app-updates': 'Fetch App Updates',
}

// Ordered: the dropdown renders in this order.
export const TASK_TYPES = [
  {
    value: 'sites',
    icon: 'lucide-globe',
    label: 'Sites',
    commands: [
      'new-site',
      'new-site-from-backup',
      'drop-site',
      'reinstall-site',
      'rename-site',
      'revert-site',
      'clear-cache',
      'wizard-setup',
    ],
  },
  {
    value: 'apps',
    icon: 'lucide-package',
    label: 'Apps',
    commands: [
      'install-app',
      'uninstall-app',
      'get-app',
      'remove-app',
      'get-and-install-app',
      'switch-branch',
      'revert-apps',
    ],
  },
  {
    value: 'backups',
    icon: 'lucide-archive',
    label: 'Backups',
    commands: ['backup-site', 'delete-backup', 'migration-backup'],
  },
  {
    value: 'updates',
    icon: 'lucide-git-pull-request-arrow',
    label: 'Updates',
    commands: [
      'update',
      'migrate',
      'retry-update',
      'revert-migration',
      'bypass-patch',
      'build',
      'update-cli',
    ],
  },
  {
    value: 'server',
    icon: 'lucide-server',
    label: 'Server',
    commands: ['setup-nginx', 'setup-letsencrypt', 'restart-services'],
  },
  // Catch-all for commands this table has not learned.
  { value: 'other', icon: 'lucide-ellipsis', label: 'Other', commands: [] },
]

const COMMAND_TYPE = Object.fromEntries(
  TASK_TYPES.flatMap(({ value, commands }) => commands.map((command) => [command, value])),
)

export const taskType = (task) => {
  return COMMAND_TYPE[task.command] || 'other'
}

export const commandLabel = (command) => {
  return (
    COMMAND_LABELS[command] || command.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  )
}

const SITE_ARG_KEY = {
  migrate: 'site',
  'migration-backup': 'site',
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

// Work with no site of its own belongs to the server. Doubles as the value the
// site filter carries, so it has to read as a label.
export const SERVER_SCOPE = 'Server'

export const siteLabel = (task) => {
  const key = SITE_ARG_KEY[task.command]
  return (key && task.args?.[key]) || SERVER_SCOPE
}

export const siteRoute = (task) => {
  const site = siteLabel(task)
  if (site === SERVER_SCOPE) return null
  return { name: 'SiteDetail', params: { name: site } }
}

// What a task ran against, so a detail header reads the same either way.
// Server-scoped work has no page of its own, so it carries no route.
export const taskScope = (task) => {
  return { label: siteLabel(task), route: siteRoute(task) }
}

const REDIRECT_ON_SUCCESS_COMMANDS = [
  'new-site',
  'install-app',
  'uninstall-app',
  'get-and-install-app',
  'drop-site',
]

const APP_ARG_KEY = {
  'install-app': 'app',
  'uninstall-app': 'app',
  'get-and-install-app': 'marketplace_app',
}

const APP_ACTION_FOR_COMMAND = {
  'install-app': 'install-app',
  'uninstall-app': 'uninstall-app',
  'get-and-install-app': 'install-app',
}

export const redirectRouteOnSuccess = (task) => {
  if (!REDIRECT_ON_SUCCESS_COMMANDS.includes(task.command)) return null
  if (task.command === 'drop-site') return { name: 'Sites' }
  const route = siteRoute(task)
  if (!route) return null
  const appKey = APP_ARG_KEY[task.command]
  const app = appKey && task.args?.[appKey]
  if (!app) return route
  return { ...route, query: { app, action: APP_ACTION_FOR_COMMAND[task.command] } }
}

/**
 * The one duration format. `precise` keeps a decimal on sub-minute values for
 * step timings, where the difference between 0.4s and 1.2s is worth reading -
 * but anything over a minute still renders as "1m 30s", never "1.5m", so two
 * durations on the same screen never disagree about what a minute looks like.
 */
export const fmtDuration = (seconds, { precise = false } = {}) => {
  if (seconds == null) return ''
  if (precise && seconds < 60) return `${seconds.toFixed(1)}s`
  const total = Math.round(seconds)
  if (total < 60) return `${total}s`
  return `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, '0')}s`
}

export const fmtDateTime = (value) => {
  if (!value) return '-'
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** A queued task has no duration, so its place in the queue takes that slot. */
export const taskDuration = (task) => {
  if (task.status === 'queued') {
    return task.queue_position ? `#${task.queue_position} in queue` : ''
  }
  return fmtDuration(task.duration_seconds)
}

export const taskLastRun = (task) => {
  return relativeTime(task.status === 'queued' ? task.queued_at : task.started_at || task.queued_at)
}
