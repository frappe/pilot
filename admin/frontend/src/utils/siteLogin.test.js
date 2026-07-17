import assert from 'node:assert/strict'
import test from 'node:test'

import { openSiteLogin } from './siteLogin.js'


function installBrowser() {
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

  await openSiteLogin(async () => {
    events.push('request')
    return link
  })

  assert.deepEqual(events[0], ['open', '', 'site-login-login-id'])
  assert.equal(events[1], 'request')
  assert.equal(popup.opener, null)
  assert.equal(popup.location, link.url)
})


test('closes the pre-opened window when link creation fails', async () => {
  const { events } = installBrowser()

  await assert.rejects(
    openSiteLogin(async () => {
      throw new Error('failed')
    }),
    /failed/,
  )

  assert.equal(events.at(-1), 'close')
})


test('closes the pre-opened window when the link has no url', async () => {
  const { events } = installBrowser()

  await assert.rejects(
    openSiteLogin(async () => ({})),
    /invalid/,
  )

  assert.equal(events.at(-1), 'close')
})
