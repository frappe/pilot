// Scope the copied editor to one app or one site's server_scripts without touching
// component code. The scope comes from the URL path (/editor/<app> or
// /editor/site/<site>), and every /api/* call is rewritten to the admin backend's
// scoped, admin-only endpoints.
const match = location.pathname.match(/\/editor\/(?:(site)\/)?([^/]+)/) || []
export const SCOPE_PARAM = match[1] || 'app'
export const SCOPE = decodeURIComponent(match[2] || '')
export const IS_SITE_SCOPE = SCOPE_PARAM === 'site'
const BASE = '/api/v1/editor'

export function scopedUrl(url) {
  if (typeof url !== 'string' || !url.startsWith('/api/')) return url
  const rest = url.slice(4) // drop leading '/api'
  const sep = rest.includes('?') ? '&' : '?'
  return `${BASE}${rest}${sep}${SCOPE_PARAM}=${encodeURIComponent(SCOPE)}`
}

const nativeFetch = window.fetch.bind(window)
window.fetch = (input, init) =>
  nativeFetch(typeof input === 'string' ? scopedUrl(input) : input, init)

const NativeEventSource = window.EventSource
window.EventSource = function (url, options) {
  return new NativeEventSource(scopedUrl(url), options)
}
window.EventSource.prototype = NativeEventSource.prototype

if (SCOPE) document.title = `${SCOPE} · Editor`
