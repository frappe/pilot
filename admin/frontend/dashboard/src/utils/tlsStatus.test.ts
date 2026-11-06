import assert from 'node:assert/strict'
import test from 'node:test'

import type { TlsStatus } from '../types/siteDomains.ts'
import { isTlsBroken, tlsBadge, tlsTooltip } from './tlsStatus.ts'

const status = (state: TlsStatus['state'], days_left: number | null = null): TlsStatus => ({
  state,
  days_left,
  expires_at: days_left === null ? null : '2026-12-01T00:00:00+00:00',
  error: state === 'failed' ? 'rate limited' : null,
  checked_at: null,
})

test('a failed, expired or expiring certificate gets a badge', () => {
  assert.deepEqual(tlsBadge(status('failed')), { label: 'Certificate failed', theme: 'red' })
  assert.deepEqual(tlsBadge(status('expired', -2)), { label: 'Certificate expired', theme: 'red' })
  assert.deepEqual(tlsBadge(status('expiring', 1)), { label: 'Expires in 1 day', theme: 'amber' })
  assert.equal(isTlsBroken(status('expiring', 9)), true)
})

test('a valid or pending certificate, or a domain without local TLS, has no badge', () => {
  assert.equal(tlsBadge(status('valid', 80)), null)
  assert.equal(tlsBadge(status('pending')), null)
  assert.equal(tlsBadge(null), null)
})

test('the tooltip falls back to the route state without a certificate record', () => {
  assert.match(tlsTooltip(status('valid', 80), true, false), /^Certificate valid until /)
  assert.equal(tlsTooltip(null, false, true), 'Certificate pending')
  assert.equal(tlsTooltip(null, true, false), 'TLS active')
})
