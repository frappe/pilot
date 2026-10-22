import assert from 'node:assert/strict'
import test from 'node:test'

import { openSiteLink } from './siteLogin.ts'

const installBrowser = () => {
  const events = []
  const popup = {
    opener: {},
    close: () => events.push('close'),
    location: '',
  }

  global.window = {
    crypto: { randomUUID: () => 'login-id' },
    open: (url, target) => {
      events.push(['open', url, target])
      return popup
    },
  }
  return { events, popup }
}

test('navigates the pre-opened window to the login link', async () => {
  const { events, popup } = installBrowser()
  const link = { url: 'http://site.localhost:7000/desk?sid=one-time-sid' }

  await openSiteLink(async () => {
    events.push('request')
    return link
  })

  assert.equal(events[0], 'request')
  assert.equal(events[1][0], 'open')
  assert.equal(events[1][1], link.url)
  assert.equal(popup.opener, null)
})

test('closes the pre-opened window when link creation fails', async () => {
  const { events } = installBrowser()

  await assert.rejects(
    openSiteLink(async () => {
      throw new Error('failed')
    }),
    /failed/,
  )

  assert.equal(events.length, 0)
})

test('closes the pre-opened window when the link has no url', async () => {
  const { events } = installBrowser()

  await assert.rejects(
    openSiteLink(async () => ({})),
    /invalid/,
  )

  assert.equal(events.length, 0)
})

test('reports the hint after the popup opens', async () => {
  installBrowser()
  const hints = []

  await openSiteLink(async () => ({ url: 'http://a.test/desk?sid=x', hint: 'add a.test to /etc/hosts' }), {
    onHint: (hint) => hints.push(hint),
  })

  assert.deepEqual(hints, ['add a.test to /etc/hosts'])
})

test('does not report a hint when the link has none', async () => {
  installBrowser()
  const hints = []

  await openSiteLink(async () => ({ url: 'http://site.localhost:7000/desk?sid=x' }), {
    onHint: (hint) => hints.push(hint),
  })

  assert.deepEqual(hints, [])
})

test('opens a regular site URL without adding login credentials', async () => {
  const { events, popup } = installBrowser()

  await openSiteLink(async () => ({ url: 'https://site.example/desk' }))

  assert.deepEqual(events, [['open', 'https://site.example/desk', '_blank']])
  assert.equal(popup.opener, null)
})
