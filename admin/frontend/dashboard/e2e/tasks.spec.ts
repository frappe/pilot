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

for (const failure of [null, { code: 'task_timeout', message: 'The task exceeded its execution time limit.' }]) {
  test(`Failure message is ${failure ? 'visible' : 'absent'}`, async ({ page }) => {
    await page.route('**/api/v1/bootstrap', (route) =>
      route.fulfill({ json: { mode: 'ready', enabled: true, name: 'test-bench' } }),
    )
    await page.route('**/api/v1/auth/session', (route) =>
      route.fulfill({ json: { authenticated: true } }),
    )
    await page.route('**/api/v1/settings', (route) => route.fulfill({ json: {} }))
    await page.route('**/api/v1/tasks/recovery-test', (route) =>
      route.fulfill({ json: {
        task_id: 'recovery-test', command: 'build', args: {}, status: 'failed',
        is_cancellable: false, failure,
      } }),
    )
    await page.route('**/api/v1/tasks/recovery-test/output/content', (route) =>
      route.fulfill({ body: 'Task output is available.' }),
    )

    await page.goto('/insights/tasks/recovery-test')
    await expect(page.getByText('Task output is available.', { exact: true })).toBeVisible()
    if (failure) {
      await expect(page.getByRole('alert')).toHaveText(failure.message)
    } else {
      await expect(page.getByRole('alert')).toHaveCount(0)
    }
  })
}
