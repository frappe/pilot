import { useColorScheme } from 'frappe-ui'
import { computed } from 'vue'

import { sidebarSections } from '@/components/navigation/list'
import { useAppMenu } from '@/components/navigation/useAppMenu'
import {
  DATABASE_SECTIONS,
  GIT_SECTIONS,
  SECURITY_SECTIONS,
  SETTINGS_PAGES,
} from '@/components/settings/sections'
import { useSession } from '@/composables/auth/useSession'

export interface SearchItem {
  name: string
  icon: string
  route?: string
  onSelect?: () => void
}

export type SearchGroups = Record<string, { items: SearchItem[] }>

export const useSearchIndex = () => {
  const { session } = useSession()
  const { setColorScheme } = useColorScheme()
  const { showBenches, showNewBench } = useAppMenu()

  return computed((): SearchGroups => {
    const groups: SearchGroups = {}

    const pages: SearchItem[] = sidebarSections
      .flatMap((section) => section.items)
      .filter((item) => !item.flag || session[item.flag as keyof typeof session])
      .map((item) => ({ name: item.label, icon: item.icon, route: item.to }))

    if (pages.length) groups.Pages = { items: pages }

    const actions: SearchItem[] = [
      { name: 'New site', icon: 'lucide-plus', route: '/sites?new=1' },
      {
        name: 'Add app from GitHub',
        icon: 'lucide-package-plus',
        route: '/marketplace?addFromGithub=1',
      },

      { name: 'Failed tasks', icon: 'lucide-list-x', route: '/insights/tasks?status=failed' },
      ...(session.allowBenchManagement
        ? [
            { name: 'New bench', icon: 'lucide-plus', onSelect: () => (showNewBench.value = true) },
            {
              name: 'Switch bench',
              icon: 'lucide-repeat',
              onSelect: () => (showBenches.value = true),
            },
          ]
        : []),

      { name: 'Settings', icon: 'lucide-settings', route: '/settings' },
      { name: 'Activity', icon: 'lucide-history', route: '/insights/activity' },
    ]

    groups.Actions = { items: actions }

    groups.Settings = {
      items: [
        ...SETTINGS_PAGES.map((page) => ({
          name: page.label,
          icon: page.icon,
          route: `/settings/${page.id}`,
        })),

        ...GIT_SECTIONS.map((section) => ({
          name: section.label,
          icon: 'lucide-git-branch',
          route: `/settings/github/${section.id}`,
        })),

        ...DATABASE_SECTIONS.map((section) => ({
          name: section.label,
          icon: 'lucide-database',
          route: `/settings/database/${section.id}`,
        })),

        ...SECURITY_SECTIONS.map((section) => ({
          name: section.label,
          icon: section.icon ?? 'lucide-shield',
          route: `/settings/security/${section.id}`,
        })),
      ],
    }

    groups.Theme = {
      items: [
        { name: 'Light', icon: 'lucide-sun', onSelect: () => setColorScheme('light') },
        { name: 'Dark', icon: 'lucide-moon', onSelect: () => setColorScheme('dark') },
        { name: 'System', icon: 'lucide-monitor', onSelect: () => setColorScheme('system') },
      ],
    }

    return groups
  })
}
