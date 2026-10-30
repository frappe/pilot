import { expect, test } from '@playwright/test'
import path from 'node:path'

const embed = path.resolve('../../backend/static/in-app-embed/cloud-settings')
const runtime = `/@fs${embed}/esm/cloud-settings.js`

test.beforeEach(async ({ page }) => {
  await page.goto('/tests/host.html')
})

test('the SDK loads the UI from Pilot and fetches a panel only when it is shown', async ({ page }) => {
  const chunks: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/esm/chunks/')) chunks.push(request.url())
  })
  await page.route('**/api/method/**', (route) => route.fulfill({ json: { message: {} } }))

  await page.evaluate(async ({ sdk, script }) => {
    const { openCloudSettings } = await import(sdk)
    await openCloudSettings({ context: { enabled: true, site_name: 'example.test', bundle: { js: script } }, panels: ['maintenance'] })
  }, { sdk: `/@fs${path.resolve('../cloud-sdk/src/dialog.ts')}`, script: `/@fs${embed}/cloud-settings.js?v=1` })

  await expect(page.getByRole('heading', { name: 'Maintenance' })).toBeVisible()
  expect(chunks.some((url) => url.includes('/MaintenancePanel-'))).toBe(true)
  expect(chunks.some((url) => url.includes('/BillingPanel-'))).toBe(false)
})

test('mount, replace, and close remove the host and restore focus', async ({ page }) => {
  await page.evaluate(async (runtime) => {
    const { mountCloudSettings } = await import(runtime)
    const trigger = document.querySelector<HTMLButtonElement>('#trigger')!
    trigger.focus()
    let closed = 0
    const options = { panels: ['maintenance'], onClose: () => { trigger.dataset.closed = String(++closed) } }
    mountCloudSettings({ enabled: true }, options)
  }, runtime)
  await expect(page.getByRole('button', { name: 'Close Cloud Settings' })).toBeVisible()
  await page.evaluate(async (runtime) => {
    const { mountCloudSettings } = await import(runtime)
    const element = document.querySelector('fc-cloud-settings') as HTMLElement & { options: object }
    mountCloudSettings({ enabled: true }, element.options)
  }, runtime)
  await expect(page.locator('fc-cloud-settings')).toHaveCount(1)
  await expect(page.locator('#trigger')).toHaveAttribute('data-closed', '1')
  await expect(page.getByRole('button', { name: 'Close Cloud Settings' })).toBeVisible()
  await page.evaluate(async (runtime) => {
    const { closeCloudSettings } = await import(runtime)
    closeCloudSettings()
    closeCloudSettings()
  }, runtime)
  await expect(page.locator('fc-cloud-settings')).toHaveCount(0)
  await expect(page.locator('#trigger')).toHaveAttribute('data-closed', '2')
  await expect(page.locator('#trigger')).toBeFocused()
})

test('a target mounts the panels inline, without the dialog or its navigation', async ({ page }) => {
  await page.route('**/api/method/**', (route) => route.fulfill({ json: { message: {} } }))
  await page.evaluate(async (runtime) => {
    const { mountCloudSettings } = await import(runtime)
    const target = document.createElement('div')
    target.id = 'inline'
    target.style.height = '600px'
    document.body.append(target)
    mountCloudSettings({ enabled: true }, { panels: ['maintenance'], target })
  }, runtime)
  await expect(page.locator('#inline fc-cloud-settings')).toHaveCount(1)
  await expect(page.getByRole('heading', { name: 'Maintenance' })).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('tab')).toHaveCount(0)
  await page.evaluate(async (runtime) => {
    const { closeCloudSettings } = await import(runtime)
    closeCloudSettings()
  }, runtime)
  await expect(page.locator('fc-cloud-settings')).toHaveCount(0)
})

test('custom translation applies to panel errors and task feedback', async ({ page }) => {
  await page.evaluate(async () => {
    const { mountCloudSettings } = await import('/src/cloud-settings/runtime.ts')
    window.fetch = async () => new Response('{}', { status: 403 })
    mountCloudSettings({ enabled: true }, {
      panels: ['usage'],
      translate: (message: string) => `Translated: ${message}`,
    })
  })
  await expect(page.getByText("Translated: You don't have permission to do this.", { exact: true })).toBeVisible()

  const messages = await page.evaluate(async () => {
    const { createStore, settleTask } = await import('/src/cloud-settings/store.ts')
    const translate = (message: string) => `Translated: ${message}`
    const store = createStore({ enabled: true }, translate)
    await store.loadBilling()
    const permission = store.state.billingError
    window.fetch = async () => { throw new TypeError('Offline') }
    await store.loadDomains()
    const connection = store.state.domainsError
    window.fetch = async () => new Response('{"message":{}}')
    await settleTask('missing', () => false, 'Failed', translate)
    return { permission, connection }
  })
  expect(messages.permission).toBe("Translated: You don't have permission to do this.")
  expect(messages.connection).toBe('Translated: Could not reach Cloud Settings. Please try again.')
  await expect(page.getByText('Translated: Still running in the background. Check back in a bit.', { exact: true })).toBeVisible()
})

test('closing during a task request cancels polling and reopening resumes the saved task', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const { waitForTask, rememberTask, getRememberedTasks } = await import('/src/cloud-settings/store.ts')
    let cancelled = false
    let resolveResponse: (response: Response) => void = () => {}
    let requests = 0
    window.fetch = () => {
      requests++
      return new Promise<Response>((resolve) => { resolveResponse = resolve })
    }
    rememberTask('example.test', 'raven', { taskId: 'install-raven', verb: 'install' })
    const pending = waitForTask('install-raven', () => cancelled)
    cancelled = true
    resolveResponse(new Response('{"message":{"status":"success","exit_code":0}}'))
    const outcome = await pending
    const remembered = getRememberedTasks('example.test').raven
    window.fetch = async () => new Response('{"message":{"status":"failed","exit_code":1}}')
    const resumed = await waitForTask(remembered.taskId)
    rememberTask('example.test', 'raven')
    return { outcome, requests, remembered, resumed, remaining: getRememberedTasks('example.test') }
  })
  expect(result).toEqual({
    outcome: 'cancelled', requests: 1,
    remembered: { taskId: 'install-raven', verb: 'install' },
    resumed: 'failed', remaining: {},
  })
})

test('the marketplace resumes a remembered task without submitting it again', async ({ page }) => {
  let taskRequests = 0
  let submissions = 0
  await page.route('**/api/method/**', async (route) => {
    const method = new URL(route.request().url()).pathname.split('.').pop()
    let message: object = {}
    if (method === 'get_marketplace_apps') {
      message = {
        apps: [{ name: 'raven', title: 'Raven', description: 'Chat', installed: false, installable: true }],
        categories: [], update_count: 0,
      }
    } else if (method === 'get_task') {
      taskRequests++
      message = { status: 'failed', exit_code: 1 }
    } else if (method === 'install_app') {
      submissions++
    }
    await route.fulfill({ json: { message } })
  })
  await page.evaluate(async (runtime) => {
    localStorage.setItem('cloud-settings:tasks:example.test', JSON.stringify({
      raven: { taskId: 'install-raven', verb: 'install' },
    }))
    const { mountCloudSettings } = await import(runtime)
    mountCloudSettings({ enabled: true, site_name: 'example.test' }, { panels: ['marketplace'] })
  }, runtime)
  await expect(page.getByRole('img', { name: "Couldn't install Raven." })).toBeVisible()
  expect(taskRequests).toBe(1)
  expect(submissions).toBe(0)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('cloud-settings:tasks:example.test')!))).toEqual({})
})

test('fills a phone screen with a panel picker and a reachable close button', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.route('**/api/method/**', (route) => route.fulfill({ json: { message: {} } }))
  await page.evaluate(async (runtime) => {
    const { mountCloudSettings } = await import(runtime)
    mountCloudSettings({ enabled: true, site_name: 'example.test' }, { panels: ['maintenance', 'advanced'] })
  }, runtime)

  const close = page.getByRole('button', { name: 'Close Cloud Settings' })
  await expect(close).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Maintenance' })).toBeVisible()
  await expect(page.locator('.cloud-settings-sidebar')).toBeHidden()

  // Wait for the opening animation to finish before measuring.
  await expect.poll(() => page.evaluate(() => {
    const dialog = document.querySelector('fc-cloud-settings')!.shadowRoot!.querySelector('[role="dialog"]')!
    return dialog.getAnimations({ subtree: true }).length
  })).toBe(0)

  const layout = await page.evaluate(() => {
    const root = document.querySelector('fc-cloud-settings')!.shadowRoot!
    const dialog = root.querySelector<HTMLElement>('[role="dialog"]')!
    const button = root.querySelector<HTMLElement>('.cloud-settings-close')!.getBoundingClientRect()
    return {
      dialogWidth: dialog.getBoundingClientRect().width,
      overflow: dialog.scrollWidth - dialog.clientWidth,
      button: { left: button.left, right: button.right, top: button.top, width: button.width, height: button.height },
    }
  })
  expect(layout.dialogWidth).toBe(375)
  expect(layout.overflow).toBeLessThanOrEqual(0)
  expect(layout.button.right).toBeLessThanOrEqual(375)
  expect(layout.button.top).toBeGreaterThanOrEqual(0)
  expect(Math.min(layout.button.width, layout.button.height)).toBeGreaterThanOrEqual(40)
})
