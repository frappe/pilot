import assert from 'node:assert/strict'
import test from 'node:test'

import {
  appsSummary,
  kindLabel,
  opTitle,
  patchSkipped,
  siteStatus,
  stateLabel,
  stateTone,
} from './migrationFormat.js'
import { fmtDateTime } from './taskFormat.js'

test('kindLabel formats update and site_migrate', () => {
  assert.equal(kindLabel('update'), 'App update')
  assert.equal(kindLabel('site_migrate'), 'Site migration')
})

test('opTitle names the operation', () => {
  const op = { kind: 'update', started_at: '2026-07-21T12:15:37+00:00' }
  assert.equal(opTitle(op), fmtDateTime(op.started_at))
  const queued = { kind: 'update', created_at: '2026-07-21T13:00:00+00:00' }
  assert.equal(opTitle(queued), fmtDateTime(queued.created_at))
  assert.equal(
    opTitle({ kind: 'site_migrate', sites: [{ name: 's1.localhost' }] }),
    'Migrate s1.localhost',
  )
  assert.equal(opTitle({ kind: 'site_migrate', sites: [] }), 'Migrate site')
})

test('appsSummary formats app list', () => {
  assert.equal(appsSummary({ apps: [] }), '')
  assert.equal(appsSummary({ apps: [{ name: 'erpnext' }] }), 'erpnext')
  assert.equal(appsSummary({ apps: [{ name: 'erpnext' }, { name: 'hrms' }] }), 'erpnext, hrms')
  assert.equal(
    appsSummary({ apps: [{ name: 'erpnext' }, { name: 'hrms' }, { name: 'crm' }] }),
    'erpnext, hrms +1',
  )
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
  assert.equal(siteStatus({ migration_status: 'running' }).label, 'Running')
  assert.equal(siteStatus({ migration_status: 'failed' }).label, 'Failed')
  assert.equal(siteStatus({ backup_status: 'backing_up' }).label, 'Backing up')
  assert.equal(siteStatus({ backup_status: 'pending' }).label, 'Pending')
})
