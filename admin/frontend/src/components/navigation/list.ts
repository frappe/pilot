export const sidebarSections = [
  {
    items: [
      { label: 'Sites', icon: 'lucide-layout-grid', to: '/sites' },
      { label: 'Marketplace', icon: 'lucide-store', to: '/marketplace' },
    ],
  },
  {
    label: 'Insights',
    items: [
      { label: 'Analytics', icon: 'lucide-chart-line', to: '/insights/analytics' },
      { label: 'Migrations', icon: 'lucide-git-pull-request-arrow', to: '/migrations' },
      { label: 'Logs', icon: 'lucide-scroll-text', to: '/insights/logs' },
      { label: 'Tasks', icon: 'lucide-list-checks', to: '/insights/tasks' },
    ],
  },
  {
    label: 'Dev tools',
    items: [
      { label: 'DB analyzer', icon: 'lucide-database', to: '/database/analyzer' },
      { label: 'SQL playground', icon: 'lucide-terminal', to: '/database/sql-playground' },
    ],
  },
]
