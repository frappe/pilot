interface SidebarNavItem {
  label: string
  icon: string
  to: string
  flag?: string
}

interface SidebarSection {
  label?: string
  items: SidebarNavItem[]
}

export const sidebarSections: SidebarSection[] = [
  {
    items: [
      { label: 'Sites', icon: 'lucide-globe', to: '/sites' },
      { label: 'Storage', icon: 'lucide-hard-drive', to: '/storage' },
      { label: 'Marketplace', icon: 'lucide-store', to: '/marketplace' },
    ],
  },
  {
    label: 'Insights',
    items: [
      { label: 'Analytics', icon: 'lucide-chart-line', to: '/insights/analytics' },
      { label: 'Updates', icon: 'lucide-git-pull-request-arrow', to: '/updates' },
      { label: 'Tasks', icon: 'lucide-list-checks', to: '/insights/tasks' },
      { label: 'Processes', icon: 'lucide-cpu', to: '/insights/processes' },
      { label: 'Logs', icon: 'lucide-scroll-text', to: '/insights/logs' },
    ],
  },
  {
    label: 'Dev tools',
    items: [
      { label: 'DB analyzer', icon: 'lucide-database', to: '/database/analyzer' },
      { label: 'SQL playground', icon: 'lucide-terminal', to: '/database/sql-playground' },
      { label: 'Code editor', icon: 'lucide-code', to: '/editor', flag: 'developerMode' },
    ],
  },
]
