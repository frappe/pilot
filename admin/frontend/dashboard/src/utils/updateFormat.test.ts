import assert from 'node:assert/strict'
import test from 'node:test'

import {
  matchesUpdateFilter,
  needsMigrate,
  opTitle,
  patchSkipped,
  skippedPatches,
  pendingActionLabel,
  siteNames,
  siteStatus,
  sitesLabel,
  stateLabel,
  stateTone,
  UPDATE_FILTERS,
} from './updateFormat.ts'
import { fmtDateTime } from './taskFormat.ts'

test('opTitle names the operation', () => {
  assert.equal(opTitle({ kind: 'update', apps_filter: ['erpnext'] }), 'Update erpnext')
  assert.equal(opTitle({ kind: 'update', apps_filter: ['erpnext', 'hrms'] }), 'Update erpnext, hrms')
  assert.equal(opTitle({ kind: 'update', apps_filter: ['a', 'b', 'c'] }), 'Update 3 apps')
  // No filter means the whole bench; a count stays stable as apps come and go.
  assert.equal(
    opTitle({ kind: 'update', apps: [{ name: 'frappe' }, { name: 'central' }] }),
    'Update 2 apps',
  )
  // Nothing resolved yet: fall back to the run time, the only thing that tells runs apart.
  const op = { kind: 'update', apps: [], started_at: '2026-07-21T12:15:37+00:00' }
  assert.equal(opTitle(op), fmtDateTime(op.started_at))
  const queued = { kind: 'update', created_at: '2026-07-21T13:00:00+00:00' }
  assert.equal(opTitle(queued), fmtDateTime(queued.created_at))
  assert.equal(
    opTitle({ kind: 'site_migrate', sites: [{ name: 's1.localhost' }] }),
    'Migrate s1.localhost',
  )
  assert.equal(opTitle({ kind: 'site_migrate', sites: [] }), 'Migrate site')
})

test('stateTone and stateLabel format operation states', () => {
  assert.equal(stateTone('completed'), 'green')
  assert.equal(stateTone('needs_attention'), 'red')
  assert.equal(stateLabel('needs_attention'), 'Needs attention')
})

test('patchSkipped detects a matching bypass_patch decision for the current diagnosis', () => {
  const op = {
    failed_site: 'site1.localhost',
    diagnosis: { patch: 'app.patches.some_patch' },
    decisions: [
      { action: 'bypass_patch', patch: 'app.patches.some_patch', site: 'site1.localhost' },
    ],
  }
  assert.equal(patchSkipped(op), true)
})

test('patchSkipped is false without a diagnosed patch', () => {
  assert.equal(patchSkipped({ diagnosis: {}, decisions: [] }), false)
})

test('patchSkipped is false when the decision is for a different patch', () => {
  const op = {
    failed_site: 'site1.localhost',
    diagnosis: { patch: 'app.patches.some_patch' },
    decisions: [
      { action: 'bypass_patch', patch: 'app.patches.other_patch', site: 'site1.localhost' },
    ],
  }
  assert.equal(patchSkipped(op), false)
})

test('patchSkipped is false when the decision is for a different site', () => {
  const op = {
    failed_site: 'site1.localhost',
    diagnosis: { patch: 'app.patches.some_patch' },
    decisions: [
      { action: 'bypass_patch', patch: 'app.patches.some_patch', site: 'site2.localhost' },
    ],
  }
  assert.equal(patchSkipped(op), false)
})

test('siteStatus formats per-site lifecycle', () => {
  assert.equal(siteStatus({ migration_status: 'recovering' }).label, 'Recovering')
  assert.equal(siteStatus({ migration_status: 'recovered' }).label, 'Recovered')
  assert.equal(siteStatus({ migration_status: 'success' }).label, 'Success')
  assert.equal(siteStatus({ migration_status: 'running' }).label, 'Migrating')
  assert.equal(siteStatus({ migration_status: 'failed' }).label, 'Failed')
  assert.equal(siteStatus({ backup_status: 'backing_up' }).label, 'Backing up')
  assert.equal(siteStatus({ backup_status: 'pending' }).label, 'Pending')
})

test('sitesLabel names one site and counts the rest', () => {
  assert.equal(sitesLabel({ sites: [{ name: 'a.localhost' }] }), 'a.localhost')
  assert.equal(sitesLabel({ sites: [{ name: 'a.localhost' }, { name: 'b.localhost' }] }), '2 sites')
  assert.equal(sitesLabel({ sites: Array(12).fill({ name: 'x' }) }), '12 sites')
})

test('sitesLabel calls an operation with no sites bench-level', () => {
  assert.equal(sitesLabel({ sites: [] }), 'Server')
  assert.equal(sitesLabel({}), 'Server')
  assert.equal(sitesLabel(null), 'Server')
})

test('siteNames spells out the full list for the tooltip', () => {
  assert.equal(
    siteNames({ sites: [{ name: 'a.localhost' }, { name: 'b.localhost' }] }),
    'a.localhost, b.localhost',
  )
  assert.equal(siteNames({}), '')
})

test('matchesUpdateFilter groups the states behind each tab', () => {
  assert.equal(matchesUpdateFilter({ state: 'needs_attention' }, 'all'), true)
  assert.equal(matchesUpdateFilter({ state: 'backing_up' }, 'active'), true)
  assert.equal(matchesUpdateFilter({ state: 'completed' }, 'active'), false)
  // A failed revert still wants a human, so it sits with the other red state.
  assert.equal(matchesUpdateFilter({ state: 'needs_attention' }, 'attention'), true)
  assert.equal(matchesUpdateFilter({ state: 'revert_failed' }, 'attention'), true)
  assert.equal(matchesUpdateFilter({ state: 'completed' }, 'completed'), true)
  assert.equal(matchesUpdateFilter({ state: 'reverted' }, 'closed'), true)
  assert.equal(matchesUpdateFilter({ state: 'dismissed' }, 'closed'), true)
})

test('matchesUpdateFilter survives an unknown filter or a stateless operation', () => {
  assert.equal(matchesUpdateFilter({ state: 'completed' }, 'nonsense'), false)
  assert.equal(matchesUpdateFilter({}, 'active'), false)
  assert.equal(matchesUpdateFilter(null, 'all'), true)
})

// The canary: a new state added without a home would be filtered out of every
// tab but All, so it would silently vanish from four of the five views.
test('every operation state belongs to exactly one tab', () => {
  const states = [
    'completed',
    'reverted',
    'dismissed',
    'needs_attention',
    'revert_failed',
    'preparing',
    'backing_up',
    'updating',
    'migrating',
    'retrying',
    'reverting_apps',
    'reverting_sites',
    'restarting',
  ]
  const tabs = UPDATE_FILTERS.filter(({ value }) => value !== 'all')
  for (const state of states) {
    const matched = tabs.filter(({ value }) => matchesUpdateFilter({ state }, value))
    assert.equal(matched.length, 1, `${state} matched ${matched.length} tabs, expected 1`)
  }
})

test('pendingActionLabel describes a queued action', () => {
  assert.equal(pendingActionLabel(null), '')
  assert.equal(pendingActionLabel({ role: 'retry', status: 'queued' }), 'Retry queued')
  assert.equal(pendingActionLabel({ role: 'bypass_patch', status: 'queued' }), 'Skip patch queued')
  assert.equal(pendingActionLabel({ role: 'restore', status: 'running' }), 'Restore in progress')
})

test('needsMigrate is true when any selected app needs a migrate or has no flag', () => {
  const updates = {
    crm: { has_migrate_changes: false },
    hrms: { has_migrate_changes: true },
    helpdesk: {},
  }
  assert.equal(needsMigrate([], updates), false)
  assert.equal(needsMigrate(['crm'], updates), false)
  assert.equal(needsMigrate(['crm', 'hrms'], updates), true)
  assert.equal(needsMigrate(['helpdesk'], updates), true)
  assert.equal(needsMigrate(['unknown'], updates), true)
})

test('skippedPatches lists only bypass_patch decisions', () => {
  const op = {
    decisions: [
      { action: 'bypass_patch', patch: 'app.patches.one', site: 'site1.localhost' },
      { action: 'dismiss', state: 'needs_attention' },
    ],
  }
  assert.deepEqual(
    skippedPatches(op).map((decision) => decision.patch),
    ['app.patches.one'],
  )
})
