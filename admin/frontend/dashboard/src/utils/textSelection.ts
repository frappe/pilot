/** A selection counts as inside the container when either end is in it, so a drag
 * that starts outside and lands in the container still suppresses autoscroll. */

export const isSelectionInside = (selection, container) => {
  if (!selection || !container || selection.isCollapsed) return false
  return container.contains(selection.anchorNode) || container.contains(selection.focusNode)
}
