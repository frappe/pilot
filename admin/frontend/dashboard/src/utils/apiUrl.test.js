import assert from 'node:assert/strict'
import test from 'node:test'

import { API_V1_PREFIX, apiErrorMessage, apiUrl, unwrap } from '../api/client.js'

test('builds relative and cross-origin v1 API URLs', () => {
  assert.equal(API_V1_PREFIX, '/api/v1')
  assert.equal(apiUrl('tasks/task-id/events'), '/api/v1/tasks/task-id/events')
  assert.equal(apiUrl('/health', 'https://admin.example.com'), 'https://admin.example.com/api/v1/health')
})

test('reads canonical and transitional API error messages', () => {
  assert.equal(apiErrorMessage({ error: { message: 'Invalid value.' } }), 'Invalid value.')
  assert.equal(apiErrorMessage({ error: 'Legacy error.' }), 'Legacy error.')
  assert.equal(apiErrorMessage({}, 'Try again.'), 'Try again.')
})

test('unwrap rethrows a resolved error body as a rejection', async () => {
  await assert.rejects(
    unwrap(Promise.resolve({ error: { message: 'System-managed and secret-like configuration keys cannot be changed.' } })),
    { message: 'System-managed and secret-like configuration keys cannot be changed.' },
  )
})

test('unwrap passes a successful payload through', async () => {
  assert.deepEqual(await unwrap(Promise.resolve({ ssl: true })), { ssl: true })
})
