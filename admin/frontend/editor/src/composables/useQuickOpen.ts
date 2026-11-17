import { reactive } from 'vue'

const state = reactive({ visible: false })

const openQuick = () => {
  state.visible = true
}

const close = () => {
  state.visible = false
}

export const useQuickOpen = () => ({ state, openQuick, close })
