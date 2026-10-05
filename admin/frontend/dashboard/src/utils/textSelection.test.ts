import test from 'node:test'
import assert from 'node:assert/strict'

import { isSelectionInside } from './textSelection.ts'

const container = {} as Node
const selectionOf = (isCollapsed: boolean, overlaps: boolean) =>
  ({
    isCollapsed,
    containsNode: (node: Node, allowPartial?: boolean) => node === container && allowPartial === true && overlaps,
  }) as Pick<Selection, 'isCollapsed' | 'containsNode'>

test('a selection overlapping the container counts, however its ends are placed', () => {
  assert.equal(isSelectionInside(selectionOf(false, true), container), true)
})

test('a selection that does not overlap the container does not count', () => {
  assert.equal(isSelectionInside(selectionOf(false, false), container), false)
})

test('a bare caret does not count', () => {
  assert.equal(isSelectionInside(selectionOf(true, true), container), false)
})

test('a missing selection or unmounted container does not count', () => {
  assert.equal(isSelectionInside(null, container), false)
  assert.equal(isSelectionInside(selectionOf(false, true), null), false)
})
