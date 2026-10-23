import { expect, test, type Page } from '@playwright/test'

const LOCKED = 'Not in the free trial. Upgrade to use it.'

const trial = {
  state: 'Active',
  ends_on: '2026-10-29',
  days_left: 7,
  deletes_on: null,
}

const billing = (onTrial: boolean) => ({
  currency: 'USD',
  plan: { name: 'Starter', subtitle: '1 vCPU · 2 GB RAM · 25 GB disk', specs: {} },
  estimate: { amount: '$0.00', note: '' },
  credit: { amount: '$0.00', note: 'Available credit', warning: false },
  payment_method: null,
  profile_complete: true,
  trial: onTrial ? trial : null,
})

const backup = {
  timestamp: '20261022_000000',
  created_at: '2026-10-22T00:00:00',
  is_offsite: false,
  files: [
    { filename: '20261022_000000-site-database.sql.gz', path: '', size_bytes: 1024, kind: 'database' },
    { filename: '20261022_000000-site-site_config_backup.json', path: '', size_bytes: 64, kind: 'site_config' },
  ],
}

// Answers every Frappe method the panel calls, the way Central and Pilot answer through the site.
async function open(page: Page, { onTrial, panels }: { onTrial: boolean; panels: string[] }) {
  await page.route('**/api/method/**', async (route) => {
    const url = new URL(route.request().url())
    const method = url.pathname.split('.').pop()
    const path = url.searchParams.get('path') ?? new URLSearchParams(route.request().postData() || '').get('path')
    const message =
      method === 'get_billing'
        ? billing(onTrial)
        : method === 'get_account_url'
          ? { url: 'https://cloud.example.test', billing: '', change_plan: '', server: 'https://cloud.example.test/servers' }
          : method === 'pilot_request' && path === 'backups'
            ? [backup]
            : {}
    await route.fulfill({ json: { message } })
  })
  await page.goto('/tests/host.html')
  await page.evaluate(async (allowed) => {
    const { mountCloudSettings } = await import('/src/cloud-settings/runtime.ts')
    mountCloudSettings({ enabled: true, site_name: 'example.test' }, { panels: allowed })
  }, panels)
}

const sidebar = (page: Page) => page.locator('.cloud-settings-sidebar')

test('a trial site does not get the site config editor', async ({ page }) => {
  await open(page, { onTrial: true, panels: ['backups', 'site-config', 'advanced'] })

  await expect(sidebar(page)).toContainText('Backups')
  await expect(sidebar(page)).not.toContainText('Site config')
})

test('a paid site keeps the site config editor', async ({ page }) => {
  await open(page, { onTrial: false, panels: ['backups', 'site-config', 'advanced'] })

  await expect(sidebar(page)).toContainText('Site config')
})

test('a trial site cannot open its server', async ({ page }) => {
  await open(page, { onTrial: true, panels: ['advanced'] })

  await expect(page.getByText(LOCKED)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open server' })).toBeDisabled()
})

test('a trial site downloads its backup files but not its config', async ({ page }) => {
  await open(page, { onTrial: true, panels: ['backups'] })

  await page.getByRole('button', { name: /Actions for/ }).click()
  await expect(page.getByText('Download Database')).toBeVisible()
  await expect(page.getByText('Download Config')).toHaveCount(0)
})

test('a trial that hides every allowed panel says why', async ({ page }) => {
  await open(page, { onTrial: true, panels: ['site-config'] })

  await expect(page.getByText(LOCKED)).toBeVisible()
})
