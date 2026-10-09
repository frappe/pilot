import assert from 'node:assert/strict'
import test from 'node:test'

import { withDateTime } from './time.ts'

test('epoch milliseconds become dates a time axis can place', () => {
  const [point, gap] = withDateTime([{ time: 1791253283236, cpu: 3.5 }, { time: null, cpu: 1 }])

  assert.deepEqual(point, { time: new Date(1791253283236), cpu: 3.5 })
  assert.equal(gap.time, null)
})
