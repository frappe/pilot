import { expect, test } from '@playwright/test'

test('Opens the new site dialog', async ({ page }) => {
  await page.goto('/sites')
  await page.getByRole('button', { name: 'New site' }).click()

  const dialog = page.getByRole('dialog', { name: 'New Site' })
  await expect(dialog.getByRole('textbox', { name: 'Site name' })).toBeVisible()
})
