import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useColorScheme } from 'frappe-ui'

import { authApi } from '@/api/auth'
import { useSession } from '@/composables/auth/useSession'

// dialogs
const showBenches = ref(false)
const showNewBench = ref(false)

// shared by mobile settings page & desktop sidebar
export const useAppMenu = () => {
  const router = useRouter()
  const { setColorScheme } = useColorScheme()
  const { session } = useSession()

  const logout = async () => {
    await authApi.logout()
    window.location.reload()
  }

  const menuItems = computed(() => [
    // Nothing behind it on a self-hosted bench.
    ...(session.centralEnabled ? [{ label: 'Central', icon: 'lucide-cloud' }] : []),
    {
      label: 'Settings',
      icon: 'lucide-settings',
      onClick: () => router.push({ name: 'Settings' }),
    },
    {
      label: 'Activity',
      icon: 'lucide-history',
      onClick: () => router.push({ name: 'Activity' }),
    },

    // Managing other benches is gated server-wide by admin.allow_bench_management.
    ...(session.allowBenchManagement
      ? [
          {
            label: 'Switch Bench',
            icon: 'lucide-repeat',
            onClick: () => (showBenches.value = true),
          },
        ]
      : []),
    {
      label: 'Theme',
      icon: 'lucide-sun-moon',
      submenu: [
        { label: 'Light', icon: 'lucide-sun', onClick: () => setColorScheme('light') },
        { label: 'Dark', icon: 'lucide-moon', onClick: () => setColorScheme('dark') },
        { label: 'System', icon: 'lucide-monitor', onClick: () => setColorScheme('system') },
      ],
    },
    { label: 'Logout', icon: 'lucide-log-out', onClick: logout },
  ])

  return { menuItems, showBenches, showNewBench, logout, session }
}
