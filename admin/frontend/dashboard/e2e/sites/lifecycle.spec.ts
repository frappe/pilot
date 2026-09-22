import { expect, test } from '@playwright/test'

const site = `e2e-${Date.now()}.localhost`

test.describe.configure({ mode: 'serial', timeout: 300_000 })

const waitForTask = async (page, landing) => {
  await expect(page).toHaveURL(/\/insights\/tasks\/.+/)
  await expect(page).toHaveURL(landing, { timeout: 240_000 })
}

const appRow = (page, name, action) =>
  page
    .locator('div')
    .filter({ has: page.getByText(name, { exact: true }) })
    .filter({ has: page.getByRole('button', { name: action }) })
    .last()

test('Create a site', async ({ page }) => {
  await page.goto('/sites')
  await page.getByRole('button', { name: 'New site' }).click()
  await page.getByRole('textbox', { name: 'Site name' }).fill(site)
  await page.getByRole('button', { name: 'Create Site' }).click()

  await waitForTask(page, new RegExp(`/sites/${site}$`))
  await expect(page.getByRole('heading', { name: site })).toBeVisible()
})

test('Install an app', async ({ page }) => {
  await page.goto(`/sites/${site}`)
  await page.getByRole('button', { name: 'Install app' }).click()
  await appRow(page, 'Blog', 'Install').getByRole('button', { name: 'Install' }).click()
  await page.getByRole('dialog', { name: 'Install Blog on' }).getByRole('button', { name: 'Install' }).click()

  await waitForTask(page, new RegExp(`/sites/${site}/apps$`))
  await expect(page.getByRole('img', { name: 'Blog', exact: true })).toBeVisible()
})

test('Uninstall the app', async ({ page }) => {
  await page.goto(`/sites/${site}`)
  await appRow(page, 'Blog', 'App actions').getByRole('button', { name: 'App actions' }).click()
  await page.getByRole('menuitem', { name: 'Uninstall' }).click()
  await page.getByRole('dialog', { name: 'Uninstall App' }).getByRole('button', { name: 'Uninstall' }).click()

  await waitForTask(page, new RegExp(`/sites/${site}/apps$`))
  await expect(page.getByRole('img', { name: 'Blog', exact: true })).toBeHidden()
})

test('Migrate the site', async ({ page }) => {
  await page.goto(`/sites/${site}/settings`)
  await page.getByRole('button', { name: 'Migrate', exact: true }).click()
  await page.getByRole('dialog', { name: 'Migrate Site' }).getByRole('button', { name: 'Migrate' }).click()

  await expect(page).toHaveURL(/\/updates\/.+/)
  await expect(page.getByText('Completed', { exact: true })).toBeVisible({ timeout: 240_000 })
})

test('Reset the site', async ({ page }) => {
  await page.goto(`/sites/${site}/settings`)
  await page.getByRole('button', { name: 'Reset site' }).click()

  const dialog = page.getByRole('dialog', { name: 'Reset Site' })
  await dialog.getByRole('textbox').fill(site)
  await dialog.getByRole('button', { name: 'Reset site' }).click()

  await expect(page).toHaveURL(/\/insights\/tasks\/.+/)
  await expect(page.getByRole('banner')).toContainText('Success', { timeout: 240_000 })
})

test('Drop the site', async ({ page }) => {
  await page.goto(`/sites/${site}/settings`)
  await page.getByRole('button', { name: 'Drop site' }).click()

  const dialog = page.getByRole('dialog', { name: 'Drop Site' })
  await dialog.getByRole('textbox').fill(site)
  await dialog.getByRole('button', { name: 'Drop site' }).click()

  await waitForTask(page, /\/sites$/)
  await expect(page.getByRole('link', { name: new RegExp(site) })).toBeHidden()
})
