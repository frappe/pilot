import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'cloud-settings.spec.ts',
  use: { baseURL: 'http://127.0.0.1:8130' },
  webServer: {
    command: 'npm exec vite -- --host 127.0.0.1 --port 8130 --strictPort',
    cwd: '../in-app-embed',
    url: 'http://127.0.0.1:8130/tests/host.html',
  },
})
