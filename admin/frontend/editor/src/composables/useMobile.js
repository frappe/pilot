import { ref } from 'vue'

const mql = window.matchMedia('(max-width: 639px)')
const isMobile = ref(mql.matches)

mql.addEventListener('change', (e) => {
  isMobile.value = e.matches
})

export function useMobile() {
  return { isMobile }
}
