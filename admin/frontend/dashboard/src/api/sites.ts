import { apiUrl, request, unwrap } from '@/api/client'
import type { DisabledApp, EnabledApp, SiteApps } from '@/types/siteApps'
import type { Backup, BackupSchedule } from '@/types/siteBackups'
import type { DnsRecords, SiteDomains } from '@/types/siteDomains'
import type { SiteAnalytics, SiteUptime } from '@/types/siteMonitoring'
import type { SiteStorageReport } from '@/types/siteStorage'
import type {
  MigrationStarted,
  SiteDetail,
  SiteLoginLink,
  SiteResource,
  WildcardDomains,
} from '@/types/sites'
import type { TaskPayload } from '@/types/tasks'

type SiteConfig = Record<string, unknown>

const inlineTimeout = 120_000

export const sitesApi = {
  list: (): Promise<SiteResource[]> => request.get('sites').json(),

  storage: (): Promise<SiteStorageReport> => request.get('sites/storage').json(),

  refreshStorage: (name: string): Promise<TaskPayload> =>
    request.post(`sites/${encodeURIComponent(name)}/actions/refresh-storage`).json(),

  detail: (name: string): Promise<SiteDetail> =>
    request.get(`sites/${encodeURIComponent(name)}`).json(),

  create: (payload: Record<string, unknown>): Promise<TaskPayload> =>
    request.post('sites', { json: payload }).json(),

  loginLink: (name: string): Promise<SiteLoginLink> =>
    request.post(`sites/${encodeURIComponent(name)}/login`).json(),

  configuration: {
    get: (name: string): Promise<SiteConfig> =>
      unwrap(request.get(`sites/${encodeURIComponent(name)}/configuration`).json()),
    update: (name: string, patch: SiteConfig): Promise<SiteConfig> =>
      unwrap(
        request.patch(`sites/${encodeURIComponent(name)}/configuration`, { json: patch }).json(),
      ),
  },

  enableTls: (name: string, email?: string): Promise<TaskPayload> =>
    request
      .post(`sites/${encodeURIComponent(name)}/actions/enable-tls`, {
        json: email ? { email } : {},
      })
      .json(),

  clearCache: (name: string): Promise<TaskPayload> =>
    request.post(`sites/${encodeURIComponent(name)}/actions/clear-cache`).json(),

  migrate: (name: string): Promise<MigrationStarted> =>
    request.post(`sites/${encodeURIComponent(name)}/actions/migrate`).json(),

  reinstall: (name: string): Promise<TaskPayload> =>
    request.post(`sites/${encodeURIComponent(name)}/actions/reinstall`).json(),

  drop: (name: string, { noBackup = false }: { noBackup?: boolean } = {}): Promise<TaskPayload> =>
    request
      .delete(`sites/${encodeURIComponent(name)}`, {
        searchParams: noBackup ? { no_backup: '1' } : {},
      })
      .json(),

  apps: {
    list: (name: string): Promise<SiteApps> =>
      request.get(`sites/${encodeURIComponent(name)}/apps`).json(),
    install: (name: string, payload: Record<string, unknown>): Promise<EnabledApp | TaskPayload> =>
      request
        .post(`sites/${encodeURIComponent(name)}/apps`, { json: payload, timeout: inlineTimeout })
        .json(),
    remove: (
      name: string,
      app: string,
      { force = false, mode = '' }: { force?: boolean; mode?: string } = {},
    ): Promise<DisabledApp | TaskPayload> =>
      request
        .delete(`sites/${encodeURIComponent(name)}/apps/${encodeURIComponent(app)}`, {
          searchParams: { ...(force ? { force: 'true' } : {}), ...(mode ? { mode } : {}) },
          timeout: inlineTimeout,
        })
        .json(),
  },

  domains: {
    list: (name: string): Promise<SiteDomains> =>
      request.get(`sites/${encodeURIComponent(name)}/domains`).json(),
    add: (name: string, domain: string): Promise<TaskPayload> =>
      request.post(`sites/${encodeURIComponent(name)}/domains`, { json: { domain } }).json(),
    remove: (name: string, domain: string): Promise<TaskPayload> =>
      request
        .delete(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}`)
        .json(),
    setPrimary: (name: string, domain: string): Promise<TaskPayload> =>
      request
        .patch(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}`, {
          json: { primary: true },
        })
        .json(),
    dnsRecords: (name: string, domain: string): Promise<DnsRecords> =>
      request
        .get(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}/dns-records`)
        .json(),
    wildcardList: (): Promise<WildcardDomains> => request.get('sites/wildcard-domains').json(),
  },

  monitoring: {
    get: (name: string, window: string): Promise<SiteAnalytics> =>
      request
        .get(`sites/${encodeURIComponent(name)}/monitoring`, { searchParams: { window } })
        .json(),
  },

  uptime: {
    get: (name: string, window: string): Promise<SiteUptime> =>
      request.get(`sites/${encodeURIComponent(name)}/uptime`, { searchParams: { window } }).json(),
  },

  backups: {
    list: (name: string, limit?: number): Promise<Backup[]> =>
      request
        .get(`sites/${encodeURIComponent(name)}/backups`, { searchParams: limit ? { limit } : {} })
        .json(),
    create: (name: string): Promise<TaskPayload> =>
      request.post(`sites/${encodeURIComponent(name)}/backups`).json(),
    download: (name: string, timestamp: string, fileId: string): string =>
      apiUrl(
        `sites/${encodeURIComponent(name)}/backups/${encodeURIComponent(timestamp)}/files/${encodeURIComponent(fileId)}/content`,
      ),
    downloadLinks: (name: string, timestamp: string): Promise<Record<string, string>> =>
      request
        .get(
          `sites/${encodeURIComponent(name)}/backups/${encodeURIComponent(timestamp)}/download-links`,
        )
        .json(),
    schedule: {
      get: (name: string): Promise<BackupSchedule> =>
        request.get(`sites/${encodeURIComponent(name)}/backup-schedule`).json(),
      set: (name: string, payload: Record<string, unknown>): Promise<BackupSchedule> =>
        request.put(`sites/${encodeURIComponent(name)}/backup-schedule`, { json: payload }).json(),
      remove: (name: string) => request.delete(`sites/${encodeURIComponent(name)}/backup-schedule`),
    },
  },
}
