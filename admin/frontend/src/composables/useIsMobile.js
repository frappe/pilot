import { computed, ref } from 'vue'

const windowWidth = ref(window.innerWidth)
window.addEventListener('resize', () => { windowWidth.value = window.innerWidth })

export function useIsMobile(breakpoint = 640) {
  return computed(() => windowWidth.value < breakpoint)
}
