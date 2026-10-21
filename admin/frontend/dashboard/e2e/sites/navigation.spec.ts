import { expect, test } from '@playwright/test'

test.describe('Site navigation actions', () => {
  const siteName = 'erp.example.com'

  test.beforeEach(async ({ page }) => {
    await page.route('**/api/v1/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      let data: object = {}
      if (path.endsWith('/bootstrap')) data = { mode: 'ready', enabled: true }
      else if (path.endsWith('/auth/session')) data = { authenticated: true }
      else if (path.endsWith('/sites')) {
        data = [{
          name: siteName,
          primary_domain: siteName,
          exists: true,
          active_apps: ['frappe'],
          broken: false,
          provisioning: false,
          setup_complete: true,
        }]
      } else if (path.endsWith('/notifications')) {
        data = { data: [], meta: { unread: 0, next_cursor: null } }
      }
      await route.fulfill({ json: data })
    })
    await page.goto('/sites')
    await page.evaluate(() => {
      window.open = (url) => {
        document.body.dataset.openedUrl = String(url)
        return window
      }
    })
    await page.getByRole('button', { name: 'Site actions' }).click()
  })

  test('Visit site fetches the regular URL without login or toasts', async ({ page }) => {
    const requests: string[] = []
    page.on('request', (request) => requests.push(new URL(request.url()).pathname))
    let releaseDetail = () => {}
    const detailReady = new Promise<void>((resolve) => { releaseDetail = resolve })
    await page.route(`**/api/v1/sites/${siteName}`, async (route) => {
      await detailReady
      await route.fulfill({ json: { url: `https://${siteName}` } })
    })

    await page.getByText('Visit site', { exact: true }).click()
    await expect.poll(() => requests).toContain(`/api/v1/sites/${siteName}`)
    await expect(page.locator('[data-sonner-toast]')).toHaveCount(0)
    releaseDetail()

    await expect(page.locator('body')).toHaveAttribute('data-opened-url', `https://${siteName}/desk`)
    expect(requests).not.toContain(`/api/v1/sites/${siteName}/login`)
    await expect(page.locator('[data-sonner-toast]')).toHaveCount(0)
  })

  test('Login as admin explicitly requests an admin login link', async ({ page }) => {
    const loginRequests: string[] = []
    const detailRequests: string[] = []
    await page.route(`**/api/v1/sites/${siteName}`, async (route) => {
      detailRequests.push(route.request().method())
      await route.fulfill({ json: { url: `https://${siteName}` } })
    })
    await page.route(`**/api/v1/sites/${siteName}/login`, async (route) => {
      loginRequests.push(route.request().method())
      await route.fulfill({ json: { url: `https://${siteName}/desk?sid=admin-session` } })
    })

    await page.getByText('Login as admin', { exact: true }).click()

    await expect(page.locator('body')).toHaveAttribute(
      'data-opened-url', `https://${siteName}/desk?sid=admin-session`,
    )
    expect(loginRequests).toEqual(['POST'])
    expect(detailRequests).toEqual([])
  })
})
