export const TOOLTIP_EDGE_GAP = 8

// Offset that centres a tooltip of `tooltipWidth` on `center`, kept inside a
// `chartWidth` track with `gap` to spare on both edges.
export function clampTooltipOffset(center, tooltipWidth, chartWidth, gap = TOOLTIP_EDGE_GAP) {
  const maxOffset = chartWidth - tooltipWidth - gap
  return Math.min(Math.max(center - tooltipWidth / 2, gap), Math.max(maxOffset, gap))
}
