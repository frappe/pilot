import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSiteAppChoices,
  findMatchingTemplate,
  getCommitUrl,
  isFrappeApp,
  resolveTemplateApps,
} from './siteApps.ts'

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

test('links a commit on GitHub from https and ssh repos', () => {
  const url = 'https://github.com/frappe/crm/commit/abc1234'

  assert.equal(getCommitUrl('https://github.com/frappe/crm', 'abc1234'), url)
  assert.equal(getCommitUrl('https://github.com/frappe/crm.git/', 'abc1234'), url)
  assert.equal(getCommitUrl('git@github.com:frappe/crm.git', 'abc1234'), url)
})

test('gives no commit link without a GitHub repo and a commit', () => {
  assert.equal(getCommitUrl('https://gitlab.com/frappe/crm', 'abc1234'), '')
  assert.equal(getCommitUrl('https://github.com/frappe/crm', ''), '')
  assert.equal(getCommitUrl(), '')
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

const choices = [{ name: 'erpnext' }, { name: 'hrms' }, { name: 'crm' }]

test('a template resolves to the apps on offer and reports the rest', () => {
  const template = { name: 'ERP', apps: ['frappe', 'hrms', 'gone', 'erpnext'] }
  assert.deepEqual(resolveTemplateApps(template, choices), {
    apps: ['hrms', 'erpnext'],
    missing: ['gone'],
  })
})

test('matches the template whose apps are the selection, in any order', () => {
  const templates = [
    { name: 'ERP', apps: ['erpnext', 'hrms'] },
    { name: 'Sales', apps: ['crm'] },
  ]
  assert.equal(findMatchingTemplate(templates, choices, ['hrms', 'erpnext'])?.name, 'ERP')
  assert.equal(findMatchingTemplate(templates, choices, ['crm'])?.name, 'Sales')
})

test('an adjusted selection matches no template', () => {
  const templates = [{ name: 'ERP', apps: ['erpnext', 'hrms'] }]
  assert.equal(findMatchingTemplate(templates, choices, ['erpnext']), undefined)
  assert.equal(findMatchingTemplate(templates, choices, ['erpnext', 'hrms', 'crm']), undefined)
})

test('a template matches on the apps still on offer', () => {
  const templates = [{ name: 'ERP', apps: ['erpnext', 'gone'] }]
  assert.equal(findMatchingTemplate(templates, choices, ['erpnext'])?.name, 'ERP')
})

test('a template with nothing on offer never matches an empty selection', () => {
  const templates = [{ name: 'Gone', apps: ['gone'] }]
  assert.equal(findMatchingTemplate(templates, choices, []), undefined)
})
