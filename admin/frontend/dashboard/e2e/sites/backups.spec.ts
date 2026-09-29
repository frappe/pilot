import { expect, test } from '@playwright/test'

test.describe.configure({ timeout: 180_000 })

let site = ''
let sites: string[] = []

test.beforeAll(async ({ request }) => {
  sites = (await (await request.get('/api/v1/sites')).json()).map((s) => s.name)
  site = sites.find((name) => !name.startsWith('e2e-')) ?? sites[0]

  const backups = await (await request.get(`/api/v1/sites/${site}/backups`)).json()
  if (backups.some((backup) => backup.files.some((file) => file.kind === 'database' && file.path)))
    return

  const { task_id } = await (await request.post(`/api/v1/sites/${site}/backups`)).json()
  await expect
    .poll(async () => (await (await request.get(`/api/v1/tasks/${task_id}`)).json()).status, {
      timeout: 150_000,
    })
    .toBe('success')
})

// The restore replaces real data, so the tests answer the request with a stub task.
const stubRestore = async (page) => {
  const sent: Record<string, unknown>[] = []
  await page.route('**/actions/restore', async (route) => {
    sent.push(route.request().postDataJSON())
    await route.fulfill({ status: 202, json: { task_id: 'e2e-restore', status: 'queued' } })
  })
  return sent
}

const openRestore = async (page, item: string) => {
  await page.goto(`/sites/${site}/backups`)
  await page.getByRole('button', { name: 'Backup actions' }).first().click()
  await page.getByRole('menuitem', { name: item, exact: true }).click()
  return page.getByRole('dialog', { name: 'Restore Backup' })
}

test('Restore Backup restores on the same site', async ({ page }) => {
  const sent = await stubRestore(page)
  const dialog = await openRestore(page, 'Restore Backup')

  await expect(dialog.getByText('Restore to')).toBeHidden()
  await expect(dialog.getByText(`The database and files of ${site} are replaced`)).toBeVisible()
  await dialog.getByRole('button', { name: 'Restore', exact: true }).click()

  await expect(page).toHaveURL(/\/tasks\/e2e-restore$/)
  expect(sent).toEqual([{ site }])
})

test('Restore Backup on Another Site needs a target site', async ({ page }) => {
  const target = sites.find((name) => name !== site)
  test.skip(!target, 'The bench has one site only.')

  const sent = await stubRestore(page)
  const dialog = await openRestore(page, 'Restore Backup on Another Site')
  const restore = dialog.getByRole('button', { name: 'Restore', exact: true })

  await expect(restore).toBeDisabled()
  await dialog.getByText('Select a site').click()
  await expect(page.getByRole('option', { name: site, exact: true })).toBeHidden()
  await page.getByRole('option', { name: target, exact: true }).click()
  await expect(dialog.getByText(`The database and files of ${target} are replaced`)).toBeVisible()
  await restore.click()

  await expect(page).toHaveURL(/\/tasks\/e2e-restore$/)
  expect(sent).toEqual([{ site: target }])
})
