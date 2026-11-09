interface SidebarNavItem {
  label: string
  icon: string
  to: string
  flag?: 'allowBenchManagement' | 'centralEnabled' | 'codeEditor'
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
      { label: 'Logs', icon: 'lucide-scroll-text', to: '/insights/logs' },
    ],
  },
  {
    label: 'Dev tools',
    items: [
      { label: 'DB analyzer', icon: 'lucide-database', to: '/database/analyzer' },
      { label: 'System console', icon: 'lucide-square-terminal', to: '/console' },
      { label: 'Code editor', icon: 'lucide-code', to: '/editor', flag: 'codeEditor' },
    ],
  },
]
