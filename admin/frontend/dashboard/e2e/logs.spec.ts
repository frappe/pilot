import { expect, test } from '@playwright/test'

test('Opens a log file', async ({ page }) => {
  await page.goto('/insights/logs')
  await page.getByRole('button', { name: /^cssutils\.log/ }).click()

  await expect(page).toHaveURL(/file=cssutils\.log/)
})

test('Searches log files', async ({ page }) => {
  await page.goto('/insights/logs')
  await page.getByPlaceholder('Search log files').fill('database')

  await expect(page.getByRole('button', { name: /^database\.log/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /^cssutils\.log/ })).toBeHidden()
})
