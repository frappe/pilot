import { expect, request } from '@playwright/test'

const globalSetup = async (config) => {
  const { baseURL, storageState } = config.projects[0].use
  const context = await request.newContext({ baseURL })

  const res = await context.post('/api/v1/auth/session', {
    data: { password: process.env.E2E_ADMIN_PASSWORD },
  })
  await expect(res).toBeOK()

  await context.storageState({ path: storageState })
  await context.dispose()
}

export default globalSetup
