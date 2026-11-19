import { computed, ref } from 'vue'

type Mode = 'dark' | 'light' | 'system'

const mql = window.matchMedia('(prefers-color-scheme: dark)')

const fromUrl = (): Mode => {
  const t = new URLSearchParams(location.search).get('theme')

  if (t === 'dark' || t === 'light') return t

  return 'system'
}

// A site embeds the editor in an iframe on its own origin; there `system` follows the host page.
// Desk sets data-theme, frappe-ui apps the dark class.
const hostRoot = (() => {
  try {
    return window.parent === window ? null : window.parent.document.documentElement
  } catch {
    return null
  }
})()

const hostTheme = () =>
  hostRoot?.getAttribute('data-theme') === 'dark' || hostRoot?.classList.contains('dark') ? 'dark' : 'light'

const mode = ref<Mode>(fromUrl())
const host = ref(hostRoot ? hostTheme() : null)

const resolved = () =>
  mode.value === 'system' ? (host.value ?? (mql.matches ? 'dark' : 'light')) : mode.value

const isDark = computed(() => resolved() === 'dark')

const apply = () => document.documentElement.setAttribute('data-theme', resolved())

mql.addEventListener('change', () => {
  if (mode.value === 'system') apply()
})

if (hostRoot)
  new MutationObserver(() => {
    host.value = hostTheme()
    apply()
  }).observe(hostRoot, { attributes: true, attributeFilter: ['data-theme', 'class'] })

export const useTheme = () => ({ mode, isDark, apply })
