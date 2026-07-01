import { request } from './client'

export const sitesApi = {
  list: () => request.get('sites/').json(),
  detail: (name) => request.get(`sites/${encodeURIComponent(name)}`).json(),
  create: (payload) => request.post('sites/create', { json: payload }).json(),
  createFromUpload: (formData) => request.post('sites/create-from-upload', { body: formData }).json(),
  login: (name) => request.post(`sites/${encodeURIComponent(name)}/login`).json(),
  config: (name, config) => request.patch(`sites/${encodeURIComponent(name)}/config`, { json: config }).json(),
  enableSsl: (name, email) => request.post(`sites/${encodeURIComponent(name)}/enable-ssl`, { json: email ? { email } : {} }).json(),
  clearCache: (name) => request.post(`sites/${encodeURIComponent(name)}/clear-cache`).json(),
  reinstall: (name) => request.post(`sites/${encodeURIComponent(name)}/reinstall`).json(),
  drop: (name) => request.post(`sites/${encodeURIComponent(name)}/drop`).json(),
  forceDrop: (name) => request.post(`sites/${encodeURIComponent(name)}/force-drop`).json(),

  apps: {
    list: (name) => request.get(`sites/${encodeURIComponent(name)}/apps`).json(),
    install: (name, app) => request.post(`sites/${encodeURIComponent(name)}/install-app`, { json: { app } }).json(),
    uninstall: (name, app) => request.post(`sites/${encodeURIComponent(name)}/uninstall-app`, { json: { app } }).json(),
    forceUninstall: (name, app) => request.post(`sites/${encodeURIComponent(name)}/force-uninstall-app`, { json: { app } }).json(),
  },

  domains: {
    list: (name) => request.get(`sites/${encodeURIComponent(name)}/domains`).json(),
    add: (name, domain) => request.post(`sites/${encodeURIComponent(name)}/domains`, { json: { domain } }).json(),
    remove: (name, domain) => request.delete(`sites/${encodeURIComponent(name)}/domains`, { json: { domain } }).json(),
    setPrimary: (name, domain) => request.post(`sites/${encodeURIComponent(name)}/domains/primary`, { json: { domain } }).json(),
    dnsRecords: (name, domain) => request.post(`sites/${encodeURIComponent(name)}/domains/dns-records`, { json: { domain } }).json(),
    wildcardList: () => request.get('sites/wildcard-domains').json(),
  },

  backups: {
    list: (name) => request.get(`sites/${encodeURIComponent(name)}/backups`).json(),
    create: (name) => request.post(`sites/${encodeURIComponent(name)}/backup`).json(),
    download: (name, filename) => `/api/sites/${encodeURIComponent(name)}/backups/download?filename=${encodeURIComponent(filename)}`,
    schedule: {
      get: (name) => request.get(`sites/${encodeURIComponent(name)}/backup-schedule`).json(),
      set: (name, schedule) => request.post(`sites/${encodeURIComponent(name)}/backup-schedule`, { json: { schedule } }).json(),
      remove: (name) => request.delete(`sites/${encodeURIComponent(name)}/backup-schedule`).json(),
    },
  },
}
