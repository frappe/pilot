import { expect, test, type Page } from '@playwright/test'

type Reply = { message?: unknown; status?: number; body?: unknown }
type Handlers = Record<string, () => Reply | Promise<Reply>>

// Frappe's get_domains shape: the site's own name first, then Pilot's attached domains.
const domains = (primary: string) => ({
  primary,
  domains: [
    { domain: 'site.local', is_default: true, is_primary: primary === 'site.local', tls: false },
    { domain: 'shop.example.com', is_default: false, is_primary: primary === 'shop.example.com', tls: true },
    { domain: 'www.example.com', is_default: false, is_primary: false, tls: true },
  ],
})

// Frappe's get_domain_dns_records lists Pilot's CNAME set, then its A set.
const records = (...entries: [string, string, string][]) => ({
  message: { records: entries.map(record) },
})

const record = ([type, host, value]: [string, string, string]) => ({ type, host, value })

// A frappe with frappe/frappe#46394 also returns Pilot's record sets as Pilot sends them.
const recordSets = (cname: [string, string, string][], a: [string, string, string][]) => ({
  message: { records: [...cname, ...a].map(record), cname: cname.map(record), a: a.map(record) },
})

const refused = (message: string) => ({
  status: 417,
  body: { exc_type: 'ValidationError', _server_messages: JSON.stringify([JSON.stringify({ message })]) },
})

async function openDomains(page: Page, primary: string, handlers: Handlers = {}) {
  await page.route('**/api/method/**', async (route) => {
    const method = new URL(route.request().url()).pathname.split('.').pop() || ''
    const reply = (await handlers[method]?.()) ?? { message: method === 'get_domains' ? domains(primary) : {} }
    await route.fulfill(reply.status ? { status: reply.status, json: reply.body } : { json: { message: reply.message } })
  })
  await page.goto('/tests/host.html')
  await page.evaluate(async () => {
    const { mountCloudSettings } = await import('/src/cloud-settings/runtime.ts')
    mountCloudSettings({ enabled: true, site_name: 'site.local' }, { panels: ['domains'] })
  })
  await expect(page.getByText('www.example.com')).toBeVisible()
}

async function continueWith(page: Page, domain: string) {
  await page.getByRole('textbox', { name: 'Domain' }).fill(domain)
  await page.getByRole('button', { name: 'Continue' }).click()
}

test('a .local site name is hidden once another domain is primary', async ({ page }) => {
  await openDomains(page, 'shop.example.com')

  await expect(page.getByText('site.local')).toHaveCount(0)
  await expect(page.getByText('shop.example.com')).toBeVisible()
})

test('a .local site name stays while it is still the primary domain', async ({ page }) => {
  await openDomains(page, 'site.local')

  await expect(page.getByText('site.local')).toBeVisible()
})

test('the primary domain cannot be removed', async ({ page }) => {
  await openDomains(page, 'shop.example.com')

  await expect(page.getByRole('button', { name: 'Actions for shop.example.com' })).toHaveCount(0)

  await page.getByRole('button', { name: 'Actions for www.example.com' }).click()
  await expect(page.getByRole('menuitem', { name: 'Make primary' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Remove' })).toBeVisible()
})

test('continue shows each record set as one option', async ({ page }) => {
  await openDomains(page, 'shop.example.com', {
    get_domain_dns_records: () =>
      records(
        ['CNAME', 'blog.example.com', 'site.local'],
        ['TXT', '_verify.blog.example.com', 'token-1'],
        ['A', 'blog.example.com', '203.0.113.10'],
      ),
  })

  await continueWith(page, 'blog.example.com')

  const dialog = page.getByRole('dialog', { name: 'Use your own domain' })
  await expect(dialog.getByText('Add either one of these records at your domain provider.')).toBeVisible()
  await expect(dialog.getByText('Option 1: CNAME record')).toBeVisible()
  await expect(dialog.getByText('Option 2: A record')).toBeVisible()
  await expect(dialog.getByRole('table').first().getByText('token-1')).toBeVisible()
  await expect(dialog.getByRole('table').nth(1).getByText('203.0.113.10')).toBeVisible()
})

test('record sets from frappe are shown as they are', async ({ page }) => {
  await openDomains(page, 'shop.example.com', {
    get_domain_dns_records: () =>
      recordSets(
        [['CNAME', 'blog.example.com', 'site.local']],
        [
          ['TXT', '_verify.blog.example.com', 'token-1'],
          ['A', 'blog.example.com', '203.0.113.10'],
        ],
      ),
  })

  await continueWith(page, 'blog.example.com')

  const dialog = page.getByRole('dialog', { name: 'Use your own domain' })
  await expect(dialog.getByText('Option 1: CNAME record')).toBeVisible()
  await expect(dialog.getByText('Option 2: A record')).toBeVisible()
  await expect(dialog.getByRole('table').first().getByText('token-1')).toHaveCount(0)
  await expect(dialog.getByRole('table').nth(1).getByText('token-1')).toBeVisible()
})

test('a refused verify keeps the records on screen until the add succeeds', async ({ page }) => {
  let attempts = 0
  await openDomains(page, 'shop.example.com', {
    get_domain_dns_records: () =>
      records(['CNAME', 'blog.example.com', 'proxy.example.net'], ['TXT', '_verify.blog.example.com', 'token-1']),
    add_domain: () => (++attempts === 1 ? refused('The TXT record for blog.example.com is not set yet.') : { message: {} }),
  })

  await continueWith(page, 'blog.example.com')
  const dialog = page.getByRole('dialog', { name: 'Use your own domain' })
  await expect(dialog.getByText('Add this record at your domain provider.')).toBeVisible()
  await expect(dialog.getByText('CNAME record', { exact: true })).toBeVisible()

  await dialog.getByRole('button', { name: 'Verify DNS' }).click()
  await expect(dialog.getByText('The TXT record for blog.example.com is not set yet.')).toBeVisible()
  await expect(dialog.getByText('token-1')).toBeVisible()

  await dialog.getByRole('button', { name: 'Verify DNS' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByText('blog.example.com added.')).toBeVisible()
})

test('a running verify blocks the other domain actions', async ({ page }) => {
  let finishAdd = () => {}
  const added = new Promise<void>((resolve) => (finishAdd = resolve))
  await openDomains(page, 'shop.example.com', {
    get_domain_dns_records: () => records(['CNAME', 'blog.example.com', 'site.local']),
    add_domain: async () => {
      await added
      return { message: {} }
    },
  })

  await continueWith(page, 'blog.example.com')
  const dialog = page.getByRole('dialog', { name: 'Use your own domain' })
  await dialog.getByRole('button', { name: 'Verify DNS' }).click()
  await dialog.getByRole('button', { name: 'Cancel' }).click()

  const actions = page.getByRole('button', { name: 'Actions for www.example.com' })
  await expect(actions).toBeDisabled()

  finishAdd()
  await expect(actions).toBeEnabled()
})

test('a failed records call shows its error', async ({ page }) => {
  await openDomains(page, 'shop.example.com', {
    get_domain_dns_records: () => refused('blog.example.com is already used by another site.'),
  })

  await continueWith(page, 'blog.example.com')

  await expect(page.getByText('blog.example.com is already used by another site.')).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'Use your own domain' })).toHaveCount(0)
})

test('no records lets the domain be registered', async ({ page }) => {
  await openDomains(page, 'shop.example.com', { get_domain_dns_records: () => records() })

  await continueWith(page, 'blog.example.com')

  const dialog = page.getByRole('dialog', { name: 'Use your own domain' })
  await expect(dialog.getByText('No DNS records needed')).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Register' })).toBeVisible()
})
