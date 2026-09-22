import { expect, test } from '@playwright/test'

const firstSite = async (page) => {
  const res = await page.request.get('/api/v1/database/sites')
  const sites = await res.json()

  return sites.find((site) => !site.name.startsWith('e2e-')).name
}

test('Shows server-wide size', async ({ page }) => {
  await page.goto('/database/analyzer')

  await expect(page.getByText('Server-wide')).toBeVisible()
  await expect(page.getByRole('term').filter({ hasText: 'Data Size' })).toBeVisible()
})

test('Shows table sizes for a site', async ({ page }) => {
  const site = await firstSite(page)

  await page.goto('/database/analyzer')
  await page.getByRole('banner').getByRole('combobox').click()
  await page.getByRole('option', { name: site, exact: true }).click()
  await page.getByRole('button', { name: 'View Details' }).click()

  const dialog = page.getByRole('dialog', { name: `Table sizes on ${site}` })
  await expect(dialog.getByRole('cell', { name: 'tabDocType', exact: true })).toBeVisible()
})

test('Kills a running query', async ({ page }) => {
  const site = await firstSite(page)
  const query = `SELECT SLEEP(${60 + (Date.now() % 60)})`
  page.request.post('/api/v1/database/queries', { data: { site, query, read_only: true } }).catch(() => {})

  await expect
    .poll(async () => (await page.request.get('/api/v1/database/processlist')).text())
    .toContain(query)

  await page.goto('/database/analyzer')
  await page.getByRole('heading', { name: 'Database Processes' }).click()

  const row = page.getByRole('row').filter({ hasText: query })
  await row.getByRole('button', { name: 'Kill' }).click()
  await page.getByRole('dialog', { name: 'Kill database process' }).getByRole('button', { name: 'Kill process' }).click()

  await expect(row).toBeHidden()
})
