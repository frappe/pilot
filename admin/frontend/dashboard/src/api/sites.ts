import { apiErrorMessage, apiUrl, request, unwrap } from '@/api/client'
import type { DisabledApp, EnabledApp, SiteApps } from '@/types/siteApps'
import type {
  Backup,
  BackupSchedule,
  BackupUploadStarted,
  BackupUploadStatus,
  FrappeCloudBackupList,
  FrappeCloudConnection,
  RemoteBackupList,
} from '@/types/siteBackups'
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

  clone: (source: string, name: string, targetBench: string): Promise<TaskPayload> =>
    request
      .post(`sites/${encodeURIComponent(source)}/actions/clone`, {
        json: { name, target_bench: targetBench },
      })
      .json(),

  loginLink: (name: string): Promise<SiteLoginLink> =>
    request.post(`sites/${encodeURIComponent(name)}/login`).json(),

  configuration: {
    get: (name: string): Promise<SiteConfig> =>
      unwrap(request.get(`sites/${encodeURIComponent(name)}/configuration`).json()),
    update: (name: string, patch: SiteConfig): Promise<SiteConfig> =>
      unwrap(
        request
          .patch(`sites/${encodeURIComponent(name)}/configuration`, {
            json: patch,
          })
          .json(),
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

  restore: (name: string, payload: Record<string, unknown>): Promise<TaskPayload> =>
    request.post(`sites/${encodeURIComponent(name)}/actions/restore`, { json: payload }).json(),

  remoteBackups: (name: string, remoteSite: string, password: string): Promise<RemoteBackupList> =>
    request
      .post(`sites/${encodeURIComponent(name)}/actions/remote-backups`, {
        json: { remote_site: remoteSite, password },
      })
      .json(),

  frappeCloud: {
    connect: (name: string, remoteSite: string): Promise<FrappeCloudConnection> =>
      unwrap(
        request
          .post(`sites/${encodeURIComponent(name)}/integrations/frappe-cloud`, {
            json: { remote_site: remoteSite },
          })
          .json(),
      ),
    connection: (name: string): Promise<FrappeCloudConnection> =>
      unwrap(request.get(`sites/${encodeURIComponent(name)}/integrations/frappe-cloud`).json()),
    disconnect: (name: string): Promise<Record<string, never>> =>
      unwrap(request.delete(`sites/${encodeURIComponent(name)}/integrations/frappe-cloud`).json()),
    backups: (name: string, start = 0): Promise<FrappeCloudBackupList> =>
      unwrap(
        request
          .get(`sites/${encodeURIComponent(name)}/integrations/frappe-cloud/backups`, {
            searchParams: { start },
          })
          .json(),
      ),
    takeBackup: (name: string): Promise<{ name: string }> =>
      unwrap(
        request.post(`sites/${encodeURIComponent(name)}/integrations/frappe-cloud/backups`).json(),
      ),
    backup: (
      name: string,
      backup: string,
    ): Promise<{ name: string; status: string; job_url: string | null }> =>
      unwrap(
        request
          .get(
            `sites/${encodeURIComponent(name)}/integrations/frappe-cloud/backups/${encodeURIComponent(backup)}`,
          )
          .json(),
      ),
  },

  // Large archives take as long as the upload does.
  uploads: {
    start: (
      name: string,
      files: Record<string, { filename: string; size: number }>,
    ): Promise<BackupUploadStarted> =>
      unwrap(request.post(`sites/${encodeURIComponent(name)}/uploads`, { json: { files } }).json()),
    status: (name: string, uploadId: string): Promise<BackupUploadStatus> =>
      unwrap(request.get(`sites/${encodeURIComponent(name)}/uploads/${uploadId}`).json()),
    // False only when the server no longer has the upload; other failures reject.
    exists: async (name: string, uploadId: string): Promise<boolean> => {
      const response = await request.get(`sites/${encodeURIComponent(name)}/uploads/${uploadId}`)
      if (response.ok) return true
      if (response.status === 404 || response.status === 422) return false
      throw Object.assign(new Error('Could not check the upload.'), { status: response.status })
    },
    cancel: (name: string, uploadId: string): Promise<Record<string, never>> =>
      unwrap(request.delete(`sites/${encodeURIComponent(name)}/uploads/${uploadId}`).json()),
    // XMLHttpRequest, because fetch cannot report upload progress.
    sendChunk: (
      name: string,
      uploadId: string,
      part: string,
      offset: number,
      chunk: Blob,
      onProgress: (sent: number) => void,
      signal: AbortSignal,
    ): Promise<number> =>
      new Promise((resolve, reject) => {
        if (signal.aborted)
          return reject(new DOMException('The upload was cancelled.', 'AbortError'))
        const xhr = new XMLHttpRequest()
        xhr.open(
          'PUT',
          apiUrl(
            `sites/${encodeURIComponent(name)}/uploads/${uploadId}/files/${part}?offset=${offset}`,
          ),
        )
        xhr.setRequestHeader('Content-Type', 'application/octet-stream')
        xhr.upload.onprogress = (event) => onProgress(event.loaded)
        xhr.onload = () => {
          const body = (() => {
            try {
              return JSON.parse(xhr.responseText)
            } catch {
              return {}
            }
          })()
          if (xhr.status < 300) resolve(body.received)
          else
            reject(
              Object.assign(new Error(apiErrorMessage(body, 'Could not upload the file.')), {
                status: xhr.status,
              }),
            )
        }
        xhr.onerror = () =>
          reject(Object.assign(new Error('The upload lost its connection.'), { status: 0 }))
        xhr.onabort = () => reject(new DOMException('The upload was cancelled.', 'AbortError'))
        signal.addEventListener('abort', () => xhr.abort(), { once: true })
        xhr.send(chunk)
      }),
  },

  buildAssets: (name: string): Promise<TaskPayload> =>
    request.post(`sites/${encodeURIComponent(name)}/actions/build-assets`).json(),

  rename: (name: string, newName: string, keepOldHostname: boolean): Promise<TaskPayload> =>
    request
      .post(`sites/${encodeURIComponent(name)}/actions/rename`, {
        json: { new_name: newName, keep_old_hostname: keepOldHostname },
      })
      .json(),

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
        .post(`sites/${encodeURIComponent(name)}/apps`, {
          json: payload,
          timeout: inlineTimeout,
        })
        .json(),
    remove: (
      name: string,
      app: string,
      { force = false, mode = '' }: { force?: boolean; mode?: string } = {},
    ): Promise<DisabledApp | TaskPayload> =>
      request
        .delete(`sites/${encodeURIComponent(name)}/apps/${encodeURIComponent(app)}`, {
          searchParams: {
            ...(force ? { force: 'true' } : {}),
            ...(mode ? { mode } : {}),
          },
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
        .get(`sites/${encodeURIComponent(name)}/monitoring`, {
          searchParams: { window },
        })
        .json(),
  },

  uptime: {
    get: (name: string, window: string): Promise<SiteUptime> =>
      request
        .get(`sites/${encodeURIComponent(name)}/uptime`, {
          searchParams: { window },
        })
        .json(),
  },

  backups: {
    list: (name: string, limit?: number): Promise<Backup[]> =>
      request
        .get(`sites/${encodeURIComponent(name)}/backups`, {
          searchParams: limit ? { limit } : {},
        })
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
        request
          .put(`sites/${encodeURIComponent(name)}/backup-schedule`, {
            json: payload,
          })
          .json(),
      remove: (name: string) => request.delete(`sites/${encodeURIComponent(name)}/backup-schedule`),
    },
  },
}
