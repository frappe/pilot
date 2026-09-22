import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/marketplace')
  await page.getByRole('button', { name: 'Import app' }).click()
})

test('Finds the app in a public repo', async ({ page }) => {
  const dialog = page.getByRole('dialog', { name: 'Import app from GitHub' })
  await dialog.getByLabel('Repository URL').fill('github.com/frappe/crm')

  await expect(dialog.getByText('Found crm')).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Import app' })).toBeEnabled()
})

test('Rejects a repo that is not a Frappe app', async ({ page }) => {
  const dialog = page.getByRole('dialog', { name: 'Import app from GitHub' })
  await dialog.getByLabel('Repository URL').fill('github.com/frappe/frappe-ui')

  await expect(dialog.getByText(/Frappe app/)).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Import app' })).toBeDisabled()
})

test('Private repos need GitHub connected', async ({ page }) => {
  const dialog = page.getByRole('dialog', { name: 'Import app from GitHub' })
  await dialog.getByRole('radio', { name: 'Your GitHub account' }).click()

  await expect(dialog.getByText('No GitHub account connected')).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Connect GitHub' })).toBeVisible()
})
