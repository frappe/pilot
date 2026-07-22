// const Placeholder = () => import('./pages/Placeholder.vue')

export const navigation = {
  Sites: {
    path: '/sites',
    icon: 'lucide-layout-grid',
    component: () => import('./pages/sites/Sites.vue'),
  },
  Marketplace: {
    path: '/marketplace',
    icon: 'lucide-store',
    component: () => import('./pages/marketplace/Marketplace.vue'),
  },
  Insights: {
    children: {
      Analytics: {
        path: '/insights/analytics',
        icon: 'lucide-chart-line',
        component: () => import('./pages/dashboard/Analytics.vue'),
      },
      Migrations: {
        path: '/migrations',
        icon: 'lucide-git-pull-request-arrow',
        component: () => import('./pages/migrations/Migrations.vue'),
      },
      Logs: {
        path: '/insights/logs',
        icon: 'lucide-scroll-text',
        component: () => import('./pages/logs/Logs.vue'),
      },
      Tasks: {
        path: '/insights/tasks',
        icon: 'lucide-list-checks',
        component: () => import('./pages/tasks/Tasks.vue'),
      },
    },
  },
  'Dev tools': {
    children: {
      'SQL playground': {
        path: '/database/sql-playground',
        icon: 'lucide-terminal',
        component: () => import('./pages/database/SQLPlayground.vue'),
      },
    },
  },
}

export function navigationRoutes(tree = navigation, group = '') {
  return Object.entries(tree).flatMap(([title, node]) =>
    node.children
      ? navigationRoutes(node.children, title)
      : [{ path: node.path, name: title, component: node.component, meta: { title, group } }],
  )
}

export function sidebarSections(tree = navigation) {
  const sections = []
  const looseItems = []
  for (const [title, node] of Object.entries(tree)) {
    if (node.children) {
      sections.push({
        label: title,
        collapsible: node.collapsible ?? false,
        items: Object.entries(node.children).map(([label, child]) => ({
          label,
          icon: child.icon,
          to: child.path,
        })),
      })
    } else {
      looseItems.push({ label: title, icon: node.icon, to: node.path })
    }
  }
  if (looseItems.length) sections.unshift({ items: looseItems })
  return sections
}
