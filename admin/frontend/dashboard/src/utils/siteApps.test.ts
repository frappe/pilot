import assert from 'node:assert/strict'
import test from 'node:test'

import { buildSiteAppChoices, isFrappeApp } from './siteApps.ts'

const frappe = (name, extra = {}) => ({
  name,
  title: name.toUpperCase(),
  repo: 'https://github.com/frappe/' + name,
  ...extra,
})

test('isFrappeApp only matches repos under the frappe org', () => {
  assert.equal(isFrappeApp({ repo: 'https://github.com/frappe/erpnext' }), true)
  assert.equal(isFrappeApp({ repo: 'https://github.com/acme/erpnext' }), false)
  assert.equal(isFrappeApp({}), false)
})

test('offers every registry app, third-party ones included', () => {
  const choices = buildSiteAppChoices(
    [frappe('erpnext'), { name: 'acme', title: 'Acme', repo: 'https://github.com/acme/acme' }],
    [],
  )
  assert.deepEqual(choices.map((a) => a.name).sort(), ['acme', 'erpnext'])
})

test('never offers frappe itself, from either source', () => {
  const choices = buildSiteAppChoices([frappe('frappe')], [{ name: 'frappe', title: 'frappe' }])
  assert.deepEqual(choices, [])
})

test('adds bench-only apps and sentence-cases their folder names', () => {
  const choices = buildSiteAppChoices([], [{ name: 'insights' }, { name: 'my_app' }])
  assert.deepEqual(
    choices.map((a) => a.title),
    ['Insights', 'My app'],
  )
})

test('a registry entry wins over the same app on the bench', () => {
  const choices = buildSiteAppChoices([frappe('hrms', { stars: 5 })], [{ name: 'hrms' }])
  assert.equal(choices.length, 1)
  assert.equal(choices[0].stars, 5, 'kept the registry entry, not the bare bench one')
})

test('orders by stars, then title, with starless apps last', () => {
  const choices = buildSiteAppChoices(
    [
      frappe('crm', { title: 'CRM', stars: 10 }),
      frappe('erpnext', { title: 'ERPNext', stars: 99 }),
      frappe('books', { title: 'Books', stars: 10 }),
    ],
    [{ name: 'zeta' }, { name: 'alpha' }],
  )
  assert.deepEqual(
    choices.map((a) => a.title),
    ['ERPNext', 'Books', 'CRM', 'Alpha', 'Zeta'],
  )
})

test('handles missing inputs without throwing', () => {
  assert.deepEqual(buildSiteAppChoices(), [])
})
