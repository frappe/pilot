import { reactive } from 'vue'

const state = reactive({ visible: false, x: 0, y: 0, items: [] })

function getPoint(e) {
  const p = e.touches?.[0] || e.changedTouches?.[0] || e
  return { x: p.clientX, y: p.clientY }
}

export function useContextMenu() {
  function open(e, items) {
    if (e && typeof e.preventDefault === 'function') e.preventDefault()
    const { x, y } = getPoint(e)
    state.x = Math.min(x, window.innerWidth - 192)
    state.y = Math.min(y, window.innerHeight - 8)
    state.items = items
    state.visible = true
  }
  function close() {
    state.visible = false
  }
  return { state, open, close }
}
