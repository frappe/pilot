import { ref } from 'vue'

const focusTick = ref(0)

const focusSearch = () => {
  focusTick.value++
}

export const useSearch = () => ({ focusTick, focusSearch })
