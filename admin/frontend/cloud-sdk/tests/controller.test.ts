import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createCloudSettings } from '../src/controller.ts'
import { isCloudSettingsAvailable } from '../src/context.ts'
import { call, CloudSettingsError } from '../src/request.ts'

test('opening twice mounts only the latest request; closing cancels a pending open', async () => {
  let resolveRuntime: (value: ReturnType<typeof makeRuntime>) => void = () => {}
  const mounted: string[] = []
  const makeRuntime = () => ({
    mountCloudSettings: (_context: unknown, options: { tab?: string }) => mounted.push(options.tab ?? ''),
    closeCloudSettings: () => mounted.push('close'),
  })
  const pending = new Promise<ReturnType<typeof makeRuntime>>((resolve) => { resolveRuntime = resolve })
  const settings = createCloudSettings(() => pending)
  const first = settings.openCloudSettings({ context: { enabled: true }, tab: 'billing' })
  const second = settings.openCloudSettings({ context: { enabled: true }, tab: 'domains' })
  resolveRuntime(makeRuntime())
  await Promise.all([first, second])
  assert.deepEqual(mounted, ['domains'])

  const cancelled = createCloudSettings(() => pending)
  const opening = cancelled.openCloudSettings({ context: { enabled: true } })
  cancelled.closeCloudSettings()
  await opening
  assert.deepEqual(mounted, ['domains'])
})

test('a failed UI load can be retried and unavailable context never loads it', async () => {
  let attempts = 0
  const settings = createCloudSettings(async () => {
    if (++attempts === 1) throw new Error('Failed to load')
    return { mountCloudSettings: () => {}, closeCloudSettings: () => {} }
  })
  await assert.rejects(settings.openCloudSettings({ context: { enabled: false } }), /not available/)
  assert.equal(attempts, 0)
  await assert.rejects(settings.openCloudSettings({ context: { enabled: true } }), /Failed to load/)
  await settings.openCloudSettings({ context: { enabled: true } })
  assert.equal(attempts, 2)
})

test('availability distinguishes permission denial from a connection failure', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => new Response('{}', { status: 403 }))
  assert.equal(await isCloudSettingsAvailable(), false)
  context.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Offline') })
  await assert.rejects(isCloudSettingsAvailable(), (error) => error instanceof CloudSettingsError && error.status === 0)
  assert.equal(await isCloudSettingsAvailable({ enabled: true }), true)
})

test('requests keep same-origin session and CSRF; invalid responses reject', async (context) => {
  Object.defineProperty(globalThis, 'csrf_token', { value: 'test-csrf', configurable: true })
  context.after(() => { Reflect.deleteProperty(globalThis, 'csrf_token') })
  const fetch = context.mock.method(globalThis, 'fetch', async () => new Response('{"message":{"ok":true}}'))
  assert.deepEqual(await call('change_plan', { plan: 'small' }), { ok: true })
  const [url, options] = fetch.mock.calls[0].arguments
  assert.equal(url, '/api/method/frappe.integrations.frappe_providers.cloud_settings.change_plan')
  assert.equal(options?.credentials, 'same-origin')
  assert.equal(options?.headers['X-Frappe-CSRF-Token'], 'test-csrf')
  context.mock.method(globalThis, 'fetch', async () => new Response('<html>Error</html>'))
  await assert.rejects(call('get_context'), (error) => error instanceof CloudSettingsError && error.excType === 'InvalidResponse')
  context.mock.method(globalThis, 'fetch', async () => new Response('null'))
  await assert.rejects(call('get_context'), (error) => error instanceof CloudSettingsError && error.excType === 'InvalidResponse')
})

test('server error messages render as plain text and malformed messages remain errors', () => {
  const error = new CloudSettingsError(400, { _server_messages: JSON.stringify([JSON.stringify({ message: '<b>Invalid domain</b>' })]) })
  assert.equal(error.message, 'Invalid domain')
  assert.equal(new CloudSettingsError(403, { _server_messages: 'invalid' }).status, 403)
})
