import { expect, test } from '@playwright/test'

test('Shows system info', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'About' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: 'About' })).toBeVisible()
  await expect(dialog.getByText('Runtime')).toBeVisible()
})

test('Lists the current session', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Sessions' }).click()

  await expect(page.getByRole('cell', { name: 'Current session' })).toBeVisible()
})
