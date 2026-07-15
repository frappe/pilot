import assert from 'node:assert/strict'
import test from 'node:test'

import { API_V1_PREFIX, apiUrl } from '../api/client.js'

test('builds relative and cross-origin v1 API URLs', () => {
  assert.equal(API_V1_PREFIX, '/api/v1')
  assert.equal(apiUrl('tasks/task-id/events'), '/api/v1/tasks/task-id/events')
  assert.equal(apiUrl('/health', 'https://admin.example.com'), 'https://admin.example.com/api/v1/health')
})
