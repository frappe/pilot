import { computed, ref } from 'vue'
import { useTheme } from 'frappe-ui'
import { authApi } from '@/api/auth'
import { useSession } from '@/composables/auth/useSession'

// dialogs
const showSettings = ref(false)
const showBenches = ref(false)
const showNewBench = ref(false)

// shared by mobile settings page & desktop sidebar
export function useAppMenu() {
  const { setTheme } = useTheme()
  const { session } = useSession()

  async function logout() {
    await authApi.logout()
    window.location.reload()
  }

  const menuItems = computed(() => [
    {
      label: 'Central',
      icon: 'lucide-cloud',
    },
    {
      label: 'Settings',
      icon: 'lucide-settings',
      onClick: () => (showSettings.value = true),
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
        { label: 'Light', icon: 'lucide-sun', onClick: () => setTheme('light') },
        { label: 'Dark', icon: 'lucide-moon', onClick: () => setTheme('dark') },
        { label: 'System', icon: 'lucide-monitor', onClick: () => setTheme('system') },
      ],
    },
    { label: 'Logout', icon: 'lucide-log-out', onClick: logout },
  ])

  return { menuItems, showSettings, showBenches, showNewBench, logout, session }
}
