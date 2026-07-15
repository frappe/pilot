import { apiUrl, request } from './client'

export const sitesApi = {
  list: () => request.get('sites').json(),
  detail: (name) => request.get(`sites/${encodeURIComponent(name)}`).json(),
  create: (payload) => request.post('sites', { json: payload }).json(),
  restore: (formData) => request.post('site-restores', { body: formData }).json(),
  loginLink: (name) => request.post(`sites/${encodeURIComponent(name)}/login-links`).json(),
  configuration: {
    get: (name) => request.get(`sites/${encodeURIComponent(name)}/configuration`).json(),
    update: (name, patch) => request.patch(`sites/${encodeURIComponent(name)}/configuration`, { json: patch }).json(),
  },
  enableTls: (name, email) => request.post(`sites/${encodeURIComponent(name)}/actions/enable-tls`, { json: email ? { email } : {} }).json(),
  clearCache: (name) => request.post(`sites/${encodeURIComponent(name)}/actions/clear-cache`).json(),
  migrate: (name) => request.post(`sites/${encodeURIComponent(name)}/actions/migrate`).json(),
  reinstall: (name) => request.post(`sites/${encodeURIComponent(name)}/actions/reinstall`).json(),
  drop: (name) => request.delete(`sites/${encodeURIComponent(name)}`).json(),

  apps: {
    list: (name) => request.get(`sites/${encodeURIComponent(name)}/apps`).json(),
    install: (name, payload) => request.post(`sites/${encodeURIComponent(name)}/apps`, { json: payload }).json(),
    remove: (name, app, { force = false } = {}) =>
      request
        .delete(`sites/${encodeURIComponent(name)}/apps/${encodeURIComponent(app)}`, {
          searchParams: force ? { force: 'true' } : {},
        })
        .json(),
  },

  domains: {
    list: (name) => request.get(`sites/${encodeURIComponent(name)}/domains`).json(),
    add: (name, domain) => request.post(`sites/${encodeURIComponent(name)}/domains`, { json: { domain } }).json(),
    remove: (name, domain) =>
      request.delete(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}`).json(),
    setPrimary: (name, domain) =>
      request
        .patch(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}`, { json: { primary: true } })
        .json(),
    dnsRecords: (name, domain) =>
      request.get(`sites/${encodeURIComponent(name)}/domains/${encodeURIComponent(domain)}/dns-records`).json(),
    wildcardList: () => request.get('sites/wildcard-domains').json(),
  },

  backups: {
    list: (name, limit) =>
      request.get(`sites/${encodeURIComponent(name)}/backups`, { searchParams: limit ? { limit } : {} }).json(),
    create: (name) => request.post(`sites/${encodeURIComponent(name)}/backups`).json(),
    download: (name, timestamp, fileId) =>
      apiUrl(
        `sites/${encodeURIComponent(name)}/backups/${encodeURIComponent(timestamp)}/files/${encodeURIComponent(fileId)}/content`,
      ),
    downloadLinks: (name, timestamp) =>
      request.get(`sites/${encodeURIComponent(name)}/backups/${encodeURIComponent(timestamp)}/download-links`).json(),
    schedule: {
      get: (name) => request.get(`sites/${encodeURIComponent(name)}/backup-schedule`).json(),
      set: (name, payload) => request.put(`sites/${encodeURIComponent(name)}/backup-schedule`, { json: payload }).json(),
      remove: (name) => request.delete(`sites/${encodeURIComponent(name)}/backup-schedule`),
    },
  },
}
