import { apiUrl, request, unwrap } from '@/api/client'

// App install and remove answer inline for a disabled app, so they wait on frappe.
const INLINE_TIMEOUT = 120_000

export const sitesApi = {
  list: () => request.get('sites').json(),
  // The report the site-storage timer refreshes; measuring again is a task.
  storage: () => request.get('sites/storage').json(),
  refreshStorage: (name) =>
    request.post(`sites/${encodeURIComponent(name)}/actions/refresh-storage`).json(),
  detail: (name) => request.get(`sites/${encodeURIComponent(name)}`).json(),
  create: (payload) => request.post('sites', { json: payload }).json(),
  loginLink: (name) => request.post(`sites/${encodeURIComponent(name)}/login`).json(),
  configuration: {
    get: (name) => unwrap(request.get(`sites/${encodeURIComponent(name)}/configuration`).json()),
    update: (name, patch) =>
      unwrap(
        request.patch(`sites/${encodeURIComponent(name)}/configuration`, { json: patch }).json(),
      ),
  },
  enableTls: (name, email) =>
    request
      .post(`sites/${encodeURIComponent(name)}/actions/enable-tls`, {
        json: email ? { email } : {},
      })
      .json(),
  clearCache: (name) =>
    request.post(`sites/${encodeURIComponent(name)}/actions/clear-cache`).json(),
  migrate: (name) => request.post(`sites/${encodeURIComponent(name)}/actions/migrate`).json(),
  reinstall: (name) => request.post(`sites/${encodeURIComponent(name)}/actions/reinstall`).json(),
  drop: (name) => request.delete(`sites/${encodeURIComponent(name)}`).json(),

  apps: {
    list: (name) => request.get(`sites/${encodeURIComponent(name)}/apps`).json(),
    install: (name, payload) =>
      request
        .post(`sites/${encodeURIComponent(name)}/apps`, { json: payload, timeout: INLINE_TIMEOUT })
        .json(),
    remove: (name, app, { force = false, mode = '' } = {}) =>
      request
        .delete(`sites/${encodeURIComponent(name)}/apps/${encodeURIComponent(app)}`, {
          searchParams: { ...(force ? { force: 'true' } : {}), ...(mode ? { mode } : {}) },
          timeout: INLINE_TIMEOUT,
        })
        .json(),
  },

  domains: {
    list: (name) => request.get(`sites/${encodeURIComponent(name)}/domains`).json(),
    add: (name, domain) =>
      request.post(`sites/${encodeURIComponent(name)}/domains`, { json: { domain } }).json(),
    remove: (name, domain) =>
      request
        .delete(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}`)
        .json(),
    setPrimary: (name, domain) =>
      request
        .patch(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}`, {
          json: { primary: true },
        })
        .json(),
    dnsRecords: (name, domain) =>
      request
        .get(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}/dns-records`)
        .json(),
    wildcardList: () => request.get('sites/wildcard-domains').json(),
  },

  monitoring: {
    get: (name, window) =>
      request
        .get(`sites/${encodeURIComponent(name)}/monitoring`, { searchParams: { window } })
        .json(),
  },

  uptime: {
    get: (name, window) =>
      request.get(`sites/${encodeURIComponent(name)}/uptime`, { searchParams: { window } }).json(),
  },

  backups: {
    list: (name, limit) =>
      request
        .get(`sites/${encodeURIComponent(name)}/backups`, { searchParams: limit ? { limit } : {} })
        .json(),
    create: (name) => request.post(`sites/${encodeURIComponent(name)}/backups`).json(),
    download: (name, timestamp, fileId) =>
      apiUrl(
        `sites/${encodeURIComponent(name)}/backups/${encodeURIComponent(timestamp)}/files/${encodeURIComponent(fileId)}/content`,
      ),
    downloadLinks: (name, timestamp) =>
      request
        .get(
          `sites/${encodeURIComponent(name)}/backups/${encodeURIComponent(timestamp)}/download-links`,
        )
        .json(),
    schedule: {
      get: (name) => request.get(`sites/${encodeURIComponent(name)}/backup-schedule`).json(),
      set: (name, payload) =>
        request.put(`sites/${encodeURIComponent(name)}/backup-schedule`, { json: payload }).json(),
      remove: (name) => request.delete(`sites/${encodeURIComponent(name)}/backup-schedule`),
    },
  },
}
