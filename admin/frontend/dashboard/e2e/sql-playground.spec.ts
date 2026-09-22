import { expect, test } from '@playwright/test'

test('Asks for a site first', async ({ page }) => {
  await page.goto('/database/sql-playground')

  await expect(page.getByText('Select a site to get started')).toBeVisible()
})

test('Opens the editor for a site', async ({ page }) => {
  await page.goto('/database/sql-playground')

  await page.getByRole('combobox').click()
  await page.getByRole('option').filter({ hasNotText: /Select site|e2e-/ }).first().click()

  await expect(page.getByRole('button', { name: 'Execute' })).toBeVisible()
})
