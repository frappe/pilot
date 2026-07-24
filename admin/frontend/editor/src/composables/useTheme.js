import { ref, computed } from 'vue'

const mql = window.matchMedia('(prefers-color-scheme: dark)')

function fromUrl() {
  const t = new URLSearchParams(location.search).get('theme')
  if (t === 'dark' || t === 'light') return t
  return 'system'
}

const mode = ref(fromUrl())

function resolved() {
  return mode.value === 'system' ? (mql.matches ? 'dark' : 'light') : mode.value
}

const isDark = computed(() => resolved() === 'dark')

function apply() {
  document.documentElement.setAttribute('data-theme', resolved())
}

mql.addEventListener('change', () => {
  if (mode.value === 'system') apply()
})

export function useTheme() {
  return { mode, isDark, apply }
}
