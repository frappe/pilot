import { expect, type Page, test } from '@playwright/test'

const note = 'This server is managed by Central, so alerts also show there.'

const mockBench = async (page: Page, central: object) => {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/', '')
    const responses: Record<string, object> = {
      bootstrap: { mode: 'admin', enabled: true, name: 'test-bench', ...central },
      'auth/session': { authenticated: true },
      settings: { bench: { onboarding: 'dismissed' }, resource_limits: { webhook_endpoints: [] } },
      notifications: { data: [], meta: { unread: 0, next_cursor: null } },
    }
    await route.fulfill({ json: responses[path] ?? {} })
  })
}

test('Links the Central note to this server in Central', async ({ page }) => {
  await mockBench(page, {
    central: true,
    central_url: 'https://central.example.com',
    central_audience: 'pilot-test',
  })
  await page.goto('/settings/general/notifications')
  const dialog = page.getByRole('dialog')

  await expect(dialog.getByText(note)).toBeVisible()
  await expect(dialog.getByRole('link', { name: 'Central', exact: true })).toHaveAttribute(
    'href',
    'https://central.example.com/dashboard/servers?pilot=pilot-test&action=overview',
  )
})

test('Shows the Central note without a link when the Central URL is unknown', async ({ page }) => {
  await mockBench(page, { central: true, central_url: '' })
  await page.goto('/settings/general/notifications')
  const dialog = page.getByRole('dialog')

  await expect(dialog.getByText(note)).toBeVisible()
  await expect(dialog.getByRole('link', { name: 'Central', exact: true })).toHaveCount(0)
})

test('Hides the Central note on a host without Central', async ({ page }) => {
  await mockBench(page, { central: false })
  await page.goto('/settings/general/notifications')
  const dialog = page.getByRole('dialog')

  await expect(dialog.getByText('Site uptime', { exact: true })).toBeVisible()
  await expect(dialog.getByText(note)).toHaveCount(0)
})

test('Asks for a webhook endpoint when none is listed', async ({ page }) => {
  await mockBench(page, { central: false })
  await page.goto('/settings/general/notifications')
  const dialog = page.getByRole('dialog')

  await expect(dialog.getByText('No webhook endpoints')).toBeVisible()
  await expect(dialog.getByText('Add one to send alerts to your own service.')).toBeVisible()
})
