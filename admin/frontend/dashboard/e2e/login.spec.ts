import { expect, test } from '@playwright/test'

test.use({ storageState: { cookies: [], origins: [] } })

test('Signed out goes to login', async ({ page }) => {
  await page.goto('/sites')

  await expect(page).toHaveURL(/\/login\?redirect=/)
  await expect(page.getByRole('heading', { name: 'Sign In' })).toBeVisible()
})

test('Wrong password shows an error', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Password').fill('not-the-password')
  await page.getByRole('button', { name: 'Continue' }).click()

  await expect(page.getByText('Incorrect password.')).toBeVisible()
})
