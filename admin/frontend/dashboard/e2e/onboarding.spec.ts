import { expect, type Page, test } from '@playwright/test'

const mockBench = async (page: Page, onboarding: string) => {
  const updates: object[] = []
  const created: object[] = []

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace('/api/v1/', '')
    const method = request.method()

    if (path === 'settings' && method === 'PATCH') updates.push(request.postDataJSON())
    if (path === 'sites' && method === 'POST') created.push(request.postDataJSON())

    const responses: Record<string, object> = {
      bootstrap: { mode: 'ready', enabled: true },
      'auth/session': { authenticated: true },
      settings: {
        bench: { onboarding },
        s3: {},
        s3_providers: [{ value: 'aws', label: 'AWS', regions: ['us-east-1'] }],
      },
      'git/connection': { connected: false, central: { available: false } },
      sites: method === 'POST' ? { task_id: 'onboarding-task' } : [],
      'sites/wildcard-domains': { domains: [] },
      apps: [],
      'marketplace/apps': [],
      notifications: { data: [], meta: { unread: 0, next_cursor: null } },
    }

    await route.fulfill({ json: responses[path] ?? {} })
  })

  return { updates, created }
}

test('Opens on a fresh bench', async ({ page }) => {
  await mockBench(page, '')
  await page.goto('/sites')

  await expect(page.getByRole('dialog').getByRole('heading', { name: 'Connect your GitHub' })).toBeVisible()
})

test('Skips to the site step and opens its task', async ({ page }) => {
  const { updates, created } = await mockBench(page, '')
  await page.goto('/sites')

  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'Skip' }).click()
  await expect(dialog.getByRole('heading', { name: 'Where should backups go?' })).toBeVisible()

  await dialog.getByRole('button', { name: 'Skip' }).click()
  await dialog.getByLabel('Site name').fill('first.localhost')
  await dialog.getByRole('button', { name: 'Create site' }).click()

  await expect(page).toHaveURL(/\/insights\/tasks\/onboarding-task$/)
  await expect(dialog).toBeHidden()
  expect(created).toEqual([{ name: 'first.localhost', apps: [] }])
  expect(updates).toContainEqual({ bench: { onboarding: 'minimized' } })
})

test('Stays in the sidebar once minimized', async ({ page }) => {
  await mockBench(page, 'minimized')
  await page.goto('/sites')

  await expect(page.getByRole('button', { name: 'Get started' })).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('Stays hidden once dismissed', async ({ page }) => {
  await mockBench(page, 'dismissed')

  const settingsLoaded = page.waitForResponse('**/api/v1/settings')
  await page.goto('/sites')
  await settingsLoaded

  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Get started' })).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
