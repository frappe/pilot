import assert from 'node:assert/strict'
import test from 'node:test'

import { regionOptionsFor } from './objectStorage.ts'

test('regionOptionsFor keeps a custom region served by a custom endpoint', () => {
  const options = regionOptionsFor(['us-east-1'], 'https://s3.example.com', 'bom')
  assert.deepEqual(
    options.map((option) => option.value),
    ['us-east-1', 'bom'],
  )
})

test('regionOptionsFor lists only the provider regions without a custom endpoint', () => {
  const options = regionOptionsFor(['us-east-1'], '', 'bom')
  assert.deepEqual(
    options.map((option) => option.value),
    ['us-east-1'],
  )
})
