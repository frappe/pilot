export interface SettingsSection {
  id: string
  label: string
  description?: string
  /** A screen opened from another section, so Back returns there. Hidden from section lists. */
  parent?: string
  icon?: string
}

export interface SettingsPage {
  id: string
  label: string
  icon: string
}

export const SETTINGS_GROUPS: { label: string; pages: SettingsPage[] }[] = [
  {
    label: 'System',
    pages: [
      { id: 'bench', label: 'Bench', icon: 'lucide-box' },
      { id: 'database', label: 'Database', icon: 'lucide-database' },
      { id: 'notifications', label: 'Notifications', icon: 'lucide-bell' },
    ],
  },
  {
    label: 'Integrations',
    pages: [
      { id: 'github', label: 'Git', icon: 'lucide-git-branch' },
      { id: 's3-bucket', label: 'Object storage', icon: 'lucide-hard-drive' },
      { id: 'llm', label: 'AI assistant', icon: 'lucide-bot' },
      { id: 'mail', label: 'Mail', icon: 'lucide-mail' },
    ],
  },
  {
    label: 'Access',
    pages: [
      { id: 'security', label: 'Security', icon: 'lucide-shield' },
      { id: 'sessions', label: 'Sessions', icon: 'lucide-monitor' },
    ],
  },
  {
    label: '',
    pages: [{ id: 'system-info', label: 'About', icon: 'lucide-info' }],
  },
]

export const SETTINGS_PAGES = SETTINGS_GROUPS.flatMap((group) => group.pages)

export const GIT_SECTIONS: SettingsSection[] = [
  {
    id: 'github-token',
    label: 'Personal access token',
    description: 'Store your own GitHub token on this server.',
  },
]

export const DATABASE_SECTIONS: SettingsSection[] = [
  {
    id: 'configurations',
    label: 'Database configurations',
    description: 'Inspect MariaDB system variables and tune guarded dynamic settings.',
  },
  {
    id: 'quick-actions',
    label: 'Quick actions',
  },
]

export const SECURITY_SECTIONS: SettingsSection[] = [
  {
    id: 'account',
    label: 'Account',
    icon: 'lucide-user-round',
    description: 'Change the admin password and set up two-factor authentication.',
  },
  {
    id: 'ssh-keys',
    label: 'SSH keys',
    icon: 'lucide-key-round',
    description: 'Manage authorized public keys for server access.',
  },
  {
    id: 'ip-rules',
    label: 'IP rules',
    icon: 'lucide-globe-lock',
    description: 'Restrict server access with IP allow and block rules.',
  },
  {
    id: 'waf',
    label: 'Web firewall',
    icon: 'lucide-shield-check',
    description: 'Inspect incoming requests for attacks before they reach your sites.',
  },
]
