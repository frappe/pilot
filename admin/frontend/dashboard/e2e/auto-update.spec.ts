import { expect, type Page, test } from '@playwright/test'

test.use({ storageState: { cookies: [], origins: [] } })

const mockHost = async (page: Page, isDev = false) => {
  let enabled = true
  let failSave = false
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/', '')
    if (path === 'cli-auto-update') {
      if (failSave) {
        return route.fulfill({ status: 500, json: { error: { message: 'Could not write the schedule.' } } })
      }
      enabled = route.request().postDataJSON().enabled
      return route.fulfill({ json: { auto_update: enabled } })
    }
    const responses: Record<string, object> = {
      bootstrap: { mode: 'admin', enabled: true, name: 'test-bench' },
      'auth/session': { authenticated: true },
      settings: { bench: { onboarding: 'dismissed' } },
      notifications: { data: [], meta: { unread: 0, next_cursor: null } },
      'cli-updates': { current_version: isDev ? 'dev' : 'v0.0.90', is_dev: isDev, auto_update: enabled },
      system: {
        os_version: 'Fedora Linux 44 (KDE Plasma Desktop Edition)',
        kernel_version: '7.0.12-201.fc44.x86_64',
        runtime: {},
      },
    }
    await route.fulfill({ json: responses[path] ?? {} })
  })
  return { failSave: () => (failSave = true) }
}

test('Saves automatic updates immediately and keeps the choice after reload', async ({ page }) => {
  await mockHost(page)
  await page.goto('/settings/system-info')
  const toggle = page.getByRole('switch', { name: 'Automatic updates' })
  await expect(page.getByText('Checks daily at 12:00 AM UTC.', { exact: true })).toBeVisible()
  await expect(page.getByText('Running on Fedora Linux 44 · kernel 7.0.12-201', { exact: true })).toBeVisible()
  await expect(toggle).toBeChecked()
  await toggle.click()
  await expect(toggle).not.toBeChecked()
  await page.reload()
  await expect(toggle).not.toBeChecked()
  await toggle.click()
  await expect(toggle).toBeChecked()
})

test('Keeps the previous choice and shows an error when saving fails', async ({ page }) => {
  const host = await mockHost(page)
  await page.goto('/settings/system-info')
  const toggle = page.getByRole('switch', { name: 'Automatic updates' })
  await expect(toggle).toBeEnabled()
  host.failSave()
  await toggle.click()
  await expect(page.getByText('Could not write the schedule.')).toBeVisible()
  await expect(toggle).toBeChecked()
})

test('Explains why source installs cannot use automatic updates', async ({ page }) => {
  await mockHost(page, true)
  await page.goto('/settings/system-info')
  await expect(page.getByRole('switch', { name: 'Automatic updates' })).toBeDisabled()
  await expect(page.getByText('Unavailable for development installs.')).toBeVisible()
})
