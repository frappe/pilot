import { reactive } from 'vue'

const state = reactive({ visible: false })

export function useQuickOpen() {
  function openQuick() {
    state.visible = true
  }
  function close() {
    state.visible = false
  }
  return { state, openQuick, close }
}
