type SelectionLike = Pick<Selection, 'isCollapsed' | 'containsNode'>

/** True when a non-empty selection overlaps the container, including drags that start, end or pass through it. */
export const isSelectionInside = (selection: SelectionLike | null, container: Node | null): boolean => {
  if (!selection || !container || selection.isCollapsed) return false
  return selection.containsNode(container, true)
}
