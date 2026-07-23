import { reactive } from 'vue'

const state = reactive({ visible: false, initial: '' })

export function useSearchModal() {
  function openSearchModal(initial = '') {
    state.initial = initial
    state.visible = true
  }
  function close() {
    state.visible = false
  }
  return { state, openSearchModal, close }
}
