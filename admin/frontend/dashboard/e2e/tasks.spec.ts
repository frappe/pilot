import { expect, test } from '@playwright/test'

test('Filters failed tasks', async ({ page }) => {
  await page.goto('/insights/tasks')
  await page.getByRole('radio', { name: 'Failed' }).click()

  await expect(page.getByRole('row').filter({ hasText: 'Success' })).toHaveCount(0)
})

test('Opens a task', async ({ page }) => {
  await page.goto('/insights/tasks')
  await page.getByRole('cell').getByRole('link').first().click()

  await expect(page).toHaveURL(/\/insights\/tasks\/.+/)
})
