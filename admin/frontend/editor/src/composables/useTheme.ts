import { computed, ref } from 'vue'

type Mode = 'dark' | 'light' | 'system'

const mql = window.matchMedia('(prefers-color-scheme: dark)')

const fromUrl = (): Mode => {
  const t = new URLSearchParams(location.search).get('theme')

  if (t === 'dark' || t === 'light') return t

  return 'system'
}

const mode = ref<Mode>(fromUrl())

const resolved = () => (mode.value === 'system' ? (mql.matches ? 'dark' : 'light') : mode.value)

const isDark = computed(() => resolved() === 'dark')

const apply = () => document.documentElement.setAttribute('data-theme', resolved())

mql.addEventListener('change', () => {
  if (mode.value === 'system') apply()
})

export const useTheme = () => ({ mode, isDark, apply })
