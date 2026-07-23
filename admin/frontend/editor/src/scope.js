// Scope the copied editor to one app without touching component code.
// The app name comes from the URL path (/editor/<app>), and every /api/*
// call is rewritten to the admin backend's scoped, admin-only endpoints.
export const APP = decodeURIComponent((location.pathname.match(/\/editor\/([^/]+)/) || [])[1] || '')
const BASE = '/api/v1/editor'

export function scopedUrl(url) {
  if (typeof url !== 'string' || !url.startsWith('/api/')) return url
  const rest = url.slice(4) // drop leading '/api'
  const sep = rest.includes('?') ? '&' : '?'
  return `${BASE}${rest}${sep}app=${encodeURIComponent(APP)}`
}

const nativeFetch = window.fetch.bind(window)
window.fetch = (input, init) =>
  nativeFetch(typeof input === 'string' ? scopedUrl(input) : input, init)

const NativeEventSource = window.EventSource
window.EventSource = function (url, options) {
  return new NativeEventSource(scopedUrl(url), options)
}
window.EventSource.prototype = NativeEventSource.prototype

if (APP) document.title = `${APP} — Editor`
