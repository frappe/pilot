import { reactive } from 'vue'

export interface MenuItem {
  label: string
  icon?: string
  danger?: boolean
  action: () => void
}

type PointerLike = MouseEvent | TouchEvent | Touch

const state = reactive({ visible: false, x: 0, y: 0, items: [] as MenuItem[] })

const getPoint = (e: PointerLike) => {
  const p = ('touches' in e && (e.touches[0] || e.changedTouches[0])) || (e as MouseEvent | Touch)

  return { x: p.clientX, y: p.clientY }
}

const open = (e: PointerLike, items: MenuItem[]) => {
  if ('preventDefault' in e) e.preventDefault()

  const { x, y } = getPoint(e)

  state.x = Math.min(x, window.innerWidth - 192)
  state.y = Math.min(y, window.innerHeight - 8)
  state.items = items
  state.visible = true
}

const close = () => {
  state.visible = false
}

export const useContextMenu = () => ({ state, open, close })
