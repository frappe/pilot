import assert from 'node:assert/strict'
import test from 'node:test'

import { isTaskActive, relativeTime, statusConfig, taskActivityLabel } from './taskFormat.js'

test('queued tasks have their own presentation', () => {
  assert.equal(statusConfig({ status: 'queued' }).label, 'Queued')
  assert.equal(statusConfig({ status: 'queued' }).theme, 'blue')
  assert.equal(taskActivityLabel({ status: 'queued', queue_position: 3 }), 'Queued · #3 in queue')
})

test('queued and running tasks are active', () => {
  assert.equal(isTaskActive({ status: 'queued' }), true)
  assert.equal(isTaskActive({ status: 'running' }), true)
  assert.equal(isTaskActive({ status: 'success' }), false)
  assert.equal(isTaskActive(null), false)
})

test('task timing tolerates a missing timestamp', () => {
  assert.equal(relativeTime(null), '')
  assert.equal(taskActivityLabel({ status: 'success', started_at: null, queued_at: null }), '')
})
