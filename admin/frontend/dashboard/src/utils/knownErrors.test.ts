import assert from 'node:assert/strict'
import test from 'node:test'

import { knownErrorMessage } from './knownErrors.ts'

test('detects a full disk in the error output', () => {
  const output = 'error An unexpected error occurred: ENOSPC: no space left on device, write'

  assert.match(knownErrorMessage('Update failed', output) ?? '', /out of disk space/)
})

test('detects a full disk in an OSError message', () => {
  assert.match(knownErrorMessage('[Errno 28] No space left on device') ?? '', /disk space/)
})

test('returns null for an error it does not know', () => {
  assert.equal(knownErrorMessage('Command yarn failed with exit code 1'), null)
  assert.equal(knownErrorMessage(undefined, null), null)
})
