import test from 'node:test'
import assert from 'node:assert/strict'

import { isSelectionInside } from './textSelection.ts'

const inside = { id: 'inside' }
const outside = { id: 'outside' }
const container = { contains: (node) => node === inside }

test('a selection with both ends inside counts', () => {
  assert.equal(
    isSelectionInside({ isCollapsed: false, anchorNode: inside, focusNode: inside }, container),
    true,
  )
})

test('a drag starting outside and ending inside counts', () => {
  assert.equal(
    isSelectionInside({ isCollapsed: false, anchorNode: outside, focusNode: inside }, container),
    true,
  )
})

test('a drag starting inside and ending outside counts', () => {
  assert.equal(
    isSelectionInside({ isCollapsed: false, anchorNode: inside, focusNode: outside }, container),
    true,
  )
})

test('a selection entirely outside does not count', () => {
  assert.equal(
    isSelectionInside({ isCollapsed: false, anchorNode: outside, focusNode: outside }, container),
    false,
  )
})

test('a bare caret does not count', () => {
  assert.equal(
    isSelectionInside({ isCollapsed: true, anchorNode: inside, focusNode: inside }, container),
    false,
  )
})

test('a missing selection or unmounted container does not count', () => {
  assert.equal(isSelectionInside(null, container), false)
  assert.equal(
    isSelectionInside({ isCollapsed: false, anchorNode: inside, focusNode: inside }, null),
    false,
  )
})
