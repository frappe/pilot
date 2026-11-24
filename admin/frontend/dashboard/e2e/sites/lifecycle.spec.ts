import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

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

const appNames = (value: unknown): string[] | null =>
  Array.isArray(value) &&
  value.every((app: unknown) => typeof app === 'string' && /^[a-z][a-z0-9_]*$/.test(app))
    ? value
    : null

const logUninstallDiagnostics = () => {
  const benchRoot = process.env.E2E_BENCH_ROOT
  if (!benchRoot || !isAbsolute(benchRoot)) {
    console.info('Uninstall diagnostics: E2E_BENCH_ROOT unavailable')
    return
  }

  let config: { status: string; apps?: string[] }
  try {
    const siteConfig = JSON.parse(
      readFileSync(join(benchRoot, 'sites', site, 'site_config.json'), 'utf8'),
    ) as { installed_apps?: unknown }
    const apps = appNames(siteConfig?.installed_apps)
    config = apps ? { status: 'ok', apps } : { status: 'invalid-installed-apps' }
  } catch {
    config = { status: 'unavailable' }
  }

  let database: { status: string; apps?: string[] }
  try {
    const env = { ...process.env }
    delete env.PYTHONPATH
    const output = execFileSync(
      join(benchRoot, 'env', 'bin', 'python'),
      ['-m', 'frappe.utils.bench_helper', 'frappe', '--site', site, 'list-apps', '--format', 'json'],
      {
        cwd: join(benchRoot, 'sites'),
        encoding: 'utf8',
        timeout: 10_000,
        maxBuffer: 64 * 1024,
        stdio: ['ignore', 'pipe', 'pipe'],
        env,
      },
    )
    const appsBySite = JSON.parse(output) as Record<string, unknown>
    const apps = appNames(appsBySite?.[site])
    database = apps ? { status: 'ok', apps } : { status: 'invalid-output' }
  } catch {
    database = { status: 'unavailable' }
  }

  console.info(`Uninstall diagnostics: ${JSON.stringify({ siteConfig: config, database })}`)
}

test.afterEach(({}, testInfo) => {
  if (
    testInfo.title === 'Uninstall the app' &&
    (testInfo.status === 'failed' || testInfo.status === 'timedOut')
  ) {
    logUninstallDiagnostics()
  }
})

test('Create a site', async ({ page }) => {
  await page.goto('/sites')
  await page.getByRole('button', { name: 'New site' }).click()
  await page.getByRole('textbox', { name: 'Site name' }).fill(site)
  await page.getByRole('button', { name: 'Create Site' }).click()

  await waitForTask(page, new RegExp(`/sites/${site}$`))
  await expect(page.getByRole('banner')).toContainText(site)
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
  await page.goto(`/sites/${site}/apps`)
  await appRow(page, 'Blog', 'App actions').getByRole('button', { name: 'App actions' }).click()
  const action = page.getByRole('menuitem', { name: /^(Remove|Uninstall)$/ })
  await expect(action).toBeVisible()
  await action.click()

  const dialog = page.getByRole('dialog', { name: /^(Remove|Uninstall) App$/ })
  await expect(dialog).toBeVisible()
  if (await page.getByRole('dialog', { name: 'Remove App' }).isVisible()) {
    await dialog.getByRole('button', { name: /^Uninstall .*stops working, and every doctype/ }).click()
  }
  await dialog.getByRole('button', { name: 'Uninstall', exact: true }).click()

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
  await page.getByRole('button', { name: 'Reset', exact: true }).click()

  const dialog = page.getByRole('dialog', { name: 'Reset Site' })
  await dialog.getByRole('textbox').fill(site)
  await dialog.getByRole('button', { name: 'Reset site' }).click()

  await expect(page).toHaveURL(/\/insights\/tasks\/.+/)
  await expect(page.getByRole('banner')).toContainText('Success', { timeout: 240_000 })
})

test('Drop the site', async ({ page }) => {
  await page.goto(`/sites/${site}/settings`)
  await page.getByRole('button', { name: 'Drop', exact: true }).click()

  const dialog = page.getByRole('dialog', { name: 'Drop Site' })
  await dialog.getByRole('textbox').fill(site)
  await dialog.getByRole('checkbox', { name: 'Take a backup before dropping' }).uncheck()

  const dropRequest = page.waitForRequest(
    (request) => request.method() === 'DELETE' && request.url().includes(`/api/v1/sites/${site}`),
  )
  await dialog.getByRole('button', { name: 'Drop site' }).click()
  expect(new URL((await dropRequest).url()).searchParams.get('no_backup')).toBe('1')

  await waitForTask(page, /\/sites$/)
  await expect(page.getByRole('link', { name: new RegExp(site) })).toBeHidden()
})
