export const GENERAL_SECTIONS = [
  {
    id: 'github',
    label: 'Git settings',
    description: 'Connect GitHub for private installs and repo browsing.',
  },
  {
    id: 's3-bucket',
    label: 'Object storage settings',
    description: 'Connect S3-compatible storage for offsite backups.',
  },
  {
    id: 'llm',
    label: 'AI assistant settings',
    description: 'Connect an LLM provider to power assistant features.',
  },
  {
    id: 'notifications',
    label: 'Notification settings',
    description: 'Alert when host resource usage crosses a limit.',
  },
  {
    id: 'mail',
    label: 'Mail settings',
    description: 'Send alerts to a mailbox alongside Central and webhooks.',
  },
  {
    id: 'workers',
    label: 'Background workers',
    description: 'Configure background worker groups and queues.',
  },
]

export const DATABASE_SECTIONS = [
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

export const SECURITY_SECTIONS = [
  {
    id: 'password',
    label: 'Change password',
    description: 'Update the password that signs in to this bench.',
  },
  {
    id: 'two-factor',
    label: 'Two-factor authentication',
    description: 'Require a code from an enrolled device at every sign-in.',
  },
  {
    id: 'firewall',
    label: 'Firewall settings',
    description: 'Restrict server access with IP allow and block rules.',
  },
  {
    id: 'waf',
    label: 'Web application firewall',
    description: 'Inspect incoming requests for attacks before they reach your sites.',
  },
  {
    id: 'ssh-keys',
    label: 'SSH keys',
    description: 'Manage authorized public keys for server access.',
  },
]
