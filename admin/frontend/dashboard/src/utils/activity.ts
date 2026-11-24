import type { AuditEntry } from '@/types/audit'
import { commandLabel } from '@/utils/taskFormat'

const typeMetaMap: Record<string, { icon: string; bg: string }> = {
  backup: { icon: 'lucide-database', bg: 'bg-surface-blue-2 text-ink-blue-7' },
  app: { icon: 'lucide-package', bg: 'bg-surface-purple-2 text-ink-purple-7' },
  ssh_key: { icon: 'lucide-key', bg: 'bg-surface-gray-2 text-ink-gray-7' },
  git: { icon: 'lucide-git-branch', bg: 'bg-surface-gray-2 text-ink-gray-7' },
  task: { icon: 'lucide-list-checks', bg: 'bg-surface-blue-2 text-ink-blue-7' },
  bypass_patch: { icon: 'lucide-wrench', bg: 'bg-surface-red-2 text-ink-red-7' },
  console_script: { icon: 'lucide-square-terminal', bg: 'bg-surface-amber-2 text-ink-amber-7' },
}
const defaultTypeMeta = {
  icon: 'lucide-activity',
  bg: 'bg-surface-gray-2 text-ink-gray-7',
}

export const activityTypeMeta = (entry: AuditEntry) => {
  const meta = typeMetaMap[entry.type] || defaultTypeMeta

  if (entry.type === 'backup' && entry.event !== 'download' && entry.event !== 'delete') {
    return {
      icon: meta.icon,
      bg:
        entry.status === 'failed'
          ? 'bg-surface-red-2 text-ink-red-7'
          : 'bg-surface-green-2 text-ink-green-7',
    }
  }

  return meta
}

export const activityTypeIcon = (type: string) => (typeMetaMap[type] || defaultTypeMeta).icon

export const activityLabel = (entry: AuditEntry) => {
  const site = entry.site ? ` on ${entry.site}` : ''

  switch (entry.type) {
    case 'backup':
      if (entry.event === 'download') return `Backup file downloaded${site}`
      if (entry.event === 'delete') return `Backup deleted${site}`
      return `Backup ${entry.status === 'failed' ? 'failed' : 'completed'}${site}`
    case 'app':
      return `App ${entry.app} ${entry.event}${site}`
    case 'ssh_key':
      return entry.event === 'added' ? 'SSH key added' : 'SSH key removed'
    case 'git':
      return entry.event === 'connected'
        ? `Connected ${entry.provider} account${entry.username ? ` (${entry.username})` : ''}`
        : 'Git account disconnected'
    case 'task':
      return `Queued ${commandLabel(entry.command || 'task')}`
    case 'bypass_patch':
      return `Bypassed patch${site}`
    case 'console_script':
      return `Ran a console script${entry.commit ? ' and committed' : ''}${site}`
    default:
      return entry.event ? `${entry.type} ${entry.event}` : entry.type
  }
}
