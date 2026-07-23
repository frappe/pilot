import { ref } from 'vue'

const focusTick = ref(0)

export function useSearch() {
  function focusSearch() {
    focusTick.value++
  }
  return { focusTick, focusSearch }
}
