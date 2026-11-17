import { reactive } from 'vue'

const state = reactive({ visible: false, initial: '' })

const openSearchModal = (initial = '') => {
  state.initial = initial
  state.visible = true
}

const close = () => {
  state.visible = false
}

export const useSearchModal = () => ({ state, openSearchModal, close })
