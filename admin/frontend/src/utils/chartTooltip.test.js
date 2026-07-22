import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { TOOLTIP_EDGE_GAP, clampTooltipOffset } from './chartTooltip.js'

const CHART = 480
const TOOLTIP = 240

test('centres the tooltip on bars away from the edges', () => {
  assert.equal(clampTooltipOffset(240, TOOLTIP, CHART), 120)
})

test('clamps the tooltip inside the left edge', () => {
  assert.equal(clampTooltipOffset(4, TOOLTIP, CHART), TOOLTIP_EDGE_GAP)
})

test('clamps the tooltip inside the right edge', () => {
  assert.equal(clampTooltipOffset(476, TOOLTIP, CHART), CHART - TOOLTIP - TOOLTIP_EDGE_GAP)
})

test('keeps a tooltip wider than the chart at the left gap', () => {
  assert.equal(clampTooltipOffset(240, CHART + 40, CHART), TOOLTIP_EDGE_GAP)
})
