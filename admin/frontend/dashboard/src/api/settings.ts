import { request, unwrap } from '@/api/client'
import type { ClientAddress, CloudflareStatus, Settings, SettingsUpdate } from '@/types/settings'
import type { CliDevUpdate, CliReleaseUpdate } from '@/types/updates'

export type CliUpdate = CliDevUpdate | CliReleaseUpdate

export const settingsApi = {
  get: (): Promise<Settings> => unwrap(request.get('settings').json()),

  update: (data: Record<string, unknown>): Promise<SettingsUpdate> =>
    unwrap(request.patch('settings', { json: data }).json()),

  changeAdminPassword: (data: Record<string, unknown>): Promise<Record<string, never>> =>
    unwrap(request.post('auth/password', { json: data }).json()),

  myIp: (): Promise<ClientAddress> => request.get('network/client').json(),

  cloudflareStatus: (): Promise<CloudflareStatus> =>
    unwrap(request.get('settings/cloudflare').json()),

  frappeStorageRegions: (): Promise<string[]> =>
    request.get('settings/s3/frappe-regions').json(),

  commonSiteConfig: {
    get: (): Promise<Record<string, unknown>> =>
      unwrap(request.get('settings/common-site-config').json()),
    update: (patch: Record<string, unknown>): Promise<Record<string, unknown>> =>
      unwrap(request.patch('settings/common-site-config', { json: patch }).json()),
    managedKeys: (): Promise<string[]> =>
      unwrap(request.get('settings/common-site-config/managed-keys').json()),
  },

  llmModels: (provider: string, apiKey = '', apiBase = ''): Promise<string[]> =>
    request
      .post('settings/llm/models', { json: { provider, api_key: apiKey, api_base: apiBase } })
      .json(),
}

export const cliUpdatesApi = {
  status: (): Promise<CliUpdate> => unwrap(request.get('cli-updates').json()),
  check: (): Promise<CliUpdate> => unwrap(request.post('cli-update-checks').json()),
  autoUpdate: (enabled: boolean): Promise<{ auto_update: boolean }> =>
    unwrap(request.patch('cli-auto-update', { json: { enabled } }).json()),
}
