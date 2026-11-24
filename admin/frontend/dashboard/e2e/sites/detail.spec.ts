import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/sites')
  const site = page
    .getByRole('link')
    .filter({ has: page.getByRole('button', { name: 'Site actions' }) })
    .filter({ hasNotText: 'e2e-' })
  await site.first().click()

  await expect(page).toHaveURL(/\/sites\/.+/)
})

test('Opens on the overview', async ({ page }) => {
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
})

test('Shows the site apps', async ({ page }) => {
  await page.getByRole('tab', { name: 'Apps' }).click()

  await expect(page).toHaveURL(/\/apps$/)
  await expect(page.getByText('Frappe', { exact: true })).toBeVisible()
})

test('Switches tabs', async ({ page }) => {
  await page.getByRole('tab', { name: 'Backups' }).click()

  await expect(page).toHaveURL(/\/backups$/)
})

test('Opens site actions', async ({ page }) => {
  await page.getByRole('button', { name: 'Site actions' }).click()

  const menu = page.getByRole('menu', { name: 'Site actions' })
  await expect(menu.getByRole('menuitem', { name: 'Back up now' })).toBeVisible()
})
