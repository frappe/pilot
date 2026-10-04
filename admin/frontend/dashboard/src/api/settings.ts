import { request, unwrap } from '@/api/client'
import type { ClientAddress, Settings, SettingsUpdate } from '@/types/settings'
import type { CliDevUpdate, CliReleaseUpdate } from '@/types/updates'

export type CliUpdate = CliDevUpdate | CliReleaseUpdate

export const settingsApi = {
  get: (): Promise<Settings> => unwrap(request.get('settings').json()),

  update: (data: Record<string, unknown>): Promise<SettingsUpdate> =>
    unwrap(request.patch('settings', { json: data }).json()),

  changeAdminPassword: (data: Record<string, unknown>): Promise<Record<string, never>> =>
    unwrap(request.post('auth/password', { json: data }).json()),

  myIp: (): Promise<ClientAddress> => request.get('network/client').json(),

  frappeStorageRegions: (): Promise<string[]> =>
    request.get('settings/s3/frappe-regions').json(),

  llmModels: (provider: string, apiKey = '', apiBase = ''): Promise<string[]> =>
    request
      .post('settings/llm/models', { json: { provider, api_key: apiKey, api_base: apiBase } })
      .json(),
}

export const cliUpdatesApi = {
  status: (): Promise<CliUpdate> => unwrap(request.get('cli-updates').json()),
  check: (): Promise<CliUpdate> => unwrap(request.post('cli-update-checks').json()),
}
