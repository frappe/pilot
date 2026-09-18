import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isTaskActive,
  isTaskCancellable,
  redirectRouteOnSuccess,
  SERVER_SCOPE,
  siteLabel,
  siteRoute,
  statusConfig,
  taskDuration,
  taskLastRun,
  taskScope,
} from './taskFormat.ts'

import { relativeTime } from './time.ts'

test('queued tasks have their own presentation', () => {
  assert.equal(statusConfig({ status: 'queued' }).label, 'Queued')
  assert.equal(statusConfig({ status: 'queued' }).theme, 'blue')
})

test('queued and running tasks are active', () => {
  assert.equal(isTaskActive({ status: 'queued' }), true)
  assert.equal(isTaskActive({ status: 'running' }), true)
  assert.equal(isTaskActive({ status: 'success' }), false)
  assert.equal(isTaskActive(null), false)
})

test('siteLabel names the site, or the server when a task has none', () => {
  assert.equal(siteLabel({ command: 'migrate', args: { site: 'a.local' } }), 'a.local')
  assert.equal(siteLabel({ command: 'new-site', args: { name: 'a.local' } }), 'a.local')
  assert.equal(siteLabel({ command: 'build', args: {} }), SERVER_SCOPE)
  assert.equal(siteLabel({ command: 'migrate', args: {} }), SERVER_SCOPE)
  assert.equal(siteLabel({ command: 'build' }), SERVER_SCOPE)
})

test('siteRoute links to the site behind a site-scoped task', () => {
  assert.deepEqual(siteRoute({ command: 'new-site', args: { name: 'a.local' } }), {
    name: 'SiteDetail',
    params: { name: 'a.local' },
  })
  assert.equal(siteRoute({ command: 'build', args: {} }), null)
})

test('taskScope names the server when a task is not bound to a site', () => {
  assert.deepEqual(taskScope({ command: 'migrate', args: { site: 'a.local' } }), {
    label: 'a.local',
    route: { name: 'SiteDetail', params: { name: 'a.local' } },
  })
  assert.deepEqual(taskScope({ command: 'build', args: {} }), {
    label: 'Server',
    route: null,
  })
})

test('site-creating and app tasks redirect to the site page on success', () => {
  assert.deepEqual(redirectRouteOnSuccess({ command: 'new-site', args: { name: 'a.local' } }), {
    name: 'SiteDetail',
    params: { name: 'a.local' },
  })
  assert.deepEqual(
    redirectRouteOnSuccess({ command: 'install-app', args: { site: 'a.local', app: 'erpnext' } }),
    {
      name: 'SiteDetail',
      params: { name: 'a.local' },
      query: { app: 'erpnext', action: 'install-app' },
    },
  )
  assert.deepEqual(
    redirectRouteOnSuccess({
      command: 'uninstall-app',
      args: { site: 'a.local', app: 'erpnext' },
    }),
    {
      name: 'SiteDetail',
      params: { name: 'a.local' },
      query: { app: 'erpnext', action: 'uninstall-app' },
    },
  )
  assert.deepEqual(
    redirectRouteOnSuccess({
      command: 'get-and-install-app',
      args: { site: 'a.local', marketplace_app: 'erpnext' },
    }),
    {
      name: 'SiteDetail',
      params: { name: 'a.local' },
      query: { app: 'erpnext', action: 'install-app' },
    },
  )
  assert.deepEqual(
    redirectRouteOnSuccess({ command: 'get-and-install-app', args: { site: 'a.local', repo: 'x' } }),
    { name: 'SiteDetail', params: { name: 'a.local' } },
  )
  // A dropped site has no detail page left to land on.
  assert.deepEqual(redirectRouteOnSuccess({ command: 'drop-site', args: { site: 'a.local' } }), {
    name: 'Sites',
  })
  assert.equal(redirectRouteOnSuccess({ command: 'backup-site', args: { site: 'a.local' } }), null)
})

test('cancelling follows the flag the backend sends', () => {
  assert.equal(isTaskCancellable({ status: 'running', is_cancellable: true }), true)
  assert.equal(isTaskCancellable({ status: 'running', is_cancellable: false }), false)
  assert.equal(isTaskCancellable({ status: 'running' }), false)
  assert.equal(isTaskCancellable(null), false)
})

test('taskDuration gives a queued task its place in the queue', () => {
  const queued = { status: 'queued', queue_position: 3, queued_at: new Date().toISOString() }
  assert.equal(taskDuration(queued), '#3 in queue')
  // Nothing has started, so a stale duration from an earlier attempt is ignored.
  assert.equal(taskDuration({ ...queued, duration_seconds: 42 }), '#3 in queue')
})

test('taskDuration is empty when the queue has not reported a position', () => {
  assert.equal(taskDuration({ status: 'queued', queued_at: new Date().toISOString() }), '')
})

test('taskDuration reports how long a finished task took', () => {
  assert.equal(taskDuration({ status: 'success', duration_seconds: 93 }), '1m 33s')
})

test('taskLastRun falls back to the queued time when a task never started', () => {
  const queuedAt = new Date(Date.now() - 3 * 60 * 1000).toISOString()
  assert.equal(taskLastRun({ status: 'killed', queued_at: queuedAt }), relativeTime(queuedAt))
})
