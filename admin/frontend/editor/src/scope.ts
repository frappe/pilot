// Scope the copied editor to one app without touching component code.
// The app name comes from the URL path (/editor/<app>), and every /api/*
// call is rewritten to the admin backend's scoped, admin-only endpoints.
export const APP = decodeURIComponent(location.pathname.match(/\/editor\/([^/]+)/)?.[1] || '')
const BASE = '/api/v1/editor'

export const scopedUrl = (url: string) => {
  if (typeof url !== 'string' || !url.startsWith('/api/')) return url

  const rest = url.slice(4) // drop leading '/api'
  const sep = rest.includes('?') ? '&' : '?'

  return `${BASE}${rest}${sep}app=${encodeURIComponent(APP)}`
}

const nativeFetch = window.fetch.bind(window)

window.fetch = (input, init) =>
  nativeFetch(typeof input === 'string' ? scopedUrl(input) : input, init)

const NativeEventSource = window.EventSource

window.EventSource = class extends NativeEventSource {
  constructor(url: string | URL, options?: EventSourceInit) {
    super(typeof url === 'string' ? scopedUrl(url) : url, options)
  }
}

if (APP) document.title = `${APP} · Editor`
