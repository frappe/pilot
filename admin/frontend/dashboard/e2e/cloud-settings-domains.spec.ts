import { expect, test, type Page } from '@playwright/test'

// Frappe's get_domains shape: the site's own name first, then Pilot's attached domains.
const domains = (primary: string) => ({
  primary,
  domains: [
    { domain: 'site.local', is_default: true, is_primary: primary === 'site.local', tls: false },
    { domain: 'shop.example.com', is_default: false, is_primary: primary === 'shop.example.com', tls: true },
    { domain: 'www.example.com', is_default: false, is_primary: false, tls: true },
  ],
})

async function openDomains(page: Page, primary: string) {
  await page.route('**/api/method/**', async (route) => {
    const method = new URL(route.request().url()).pathname.split('.').pop()
    await route.fulfill({ json: { message: method === 'get_domains' ? domains(primary) : {} } })
  })
  await page.goto('/tests/host.html')
  await page.evaluate(async () => {
    const { mountCloudSettings } = await import('/src/cloud-settings/runtime.ts')
    mountCloudSettings({ enabled: true, site_name: 'site.local' }, { panels: ['domains'] })
  })
  await expect(page.getByText('www.example.com')).toBeVisible()
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
