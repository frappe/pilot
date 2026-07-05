// const Placeholder = () => import('./pages/Placeholder.vue')

export const navigation = {
  Sites: {
    labelKey: 'navigation.sites',
    path: '/sites',
    icon: 'lucide-layout-grid',
    component: () => import('./pages/Sites.vue'),
  },
  Marketplace: {
    labelKey: 'navigation.marketplace',
    path: '/marketplace',
    icon: 'lucide-store',
    component: () => import('./pages/Marketplace.vue'),
  },
  Insights: {
    labelKey: 'navigation.insights',
    children: {
      Analytics: {
        labelKey: 'navigation.analytics',
        path: '/insights/analytics',
        icon: 'lucide-chart-line',
        component: () => import('./pages/Analytics.vue'),
      },
      Logs: {
        labelKey: 'navigation.logs',
        path: '/insights/logs',
        icon: 'lucide-scroll-text',
        component: () => import('./pages/Logs.vue'),
      },
      Tasks: {
        labelKey: 'navigation.tasks',
        path: '/insights/tasks',
        icon: 'lucide-list-checks',
        component: () => import('./pages/Tasks.vue'),
      },
    },
  },
  'Dev tools': {
    labelKey: 'navigation.devTools',
    children: {
      // 'DB analyzer': {
      //   path: '/database/analyzer',
      //   icon: 'lucide-database',
      //   component: Placeholder,
      // },
      'SQL playground': {
        labelKey: 'navigation.sqlPlayground',
        path: '/database/sql-playground',
        icon: 'lucide-terminal',
        component: () => import('./pages/SQLPlayground.vue'),
      },
    },
  },
}

export function navigationRoutes(tree = navigation, group = '') {
  return Object.entries(tree).flatMap(([title, node]) =>
    node.children
      ? navigationRoutes(node.children, node.labelKey)
      : [{ path: node.path, name: title, component: node.component, meta: { titleKey: node.labelKey, groupKey: group } }],
  )
}

export function sidebarSections(t, tree = navigation) {
  const sections = []
  const looseItems = []
  for (const [title, node] of Object.entries(tree)) {
    if (node.children) {
      sections.push({
        label: t(node.labelKey),
        collapsible: node.collapsible ?? false,
        items: Object.entries(node.children).map(([, child]) => ({
          label: t(child.labelKey),
          icon: child.icon,
          to: child.path,
        })),
      })
    } else {
      looseItems.push({ label: t(node.labelKey), icon: node.icon, to: node.path })
    }
  }
  if (looseItems.length) sections.unshift({ items: looseItems })
  return sections
}
