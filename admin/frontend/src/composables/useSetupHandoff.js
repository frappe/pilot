import { ref } from 'vue'

const awaitingTerminal = ref(false)

export function useSetupHandoff() {
  return { awaitingTerminal }
}
