import ky from 'ky'
import { APP } from '@/scope'

// The editor backend lives under /api/v1/editor and is scoped per app via an
// ?app= query param (see scope.js). ky passes Request objects, which the global
// fetch shim leaves untouched, so we scope here in a beforeRequest hook instead.
export const request = ky.create({
  prefix: '/api/v1/editor',
  throwHttpErrors: false,
  // ky defaults to 10s; git/mariadb operations can legitimately run longer.
  timeout: 60_000,
  hooks: {
    beforeRequest: [
      ({ request }) => {
        const url = new URL(request.url)
        if (!url.searchParams.has('app')) url.searchParams.set('app', APP)
        return new Request(url, request)
      },
    ],
  },
})

// Parse a JSON body. With a fallback, returns it on any failed request; without
// one, throws the server's message. Empty bodies yield the fallback or null.
export async function body(res, fallback) {
  const hasFallback = arguments.length > 1
  if (!res.ok) {
    if (hasFallback) return fallback
    throw new Error(await errorMessage(res))
  }
  const text = await res.text()
  return text ? JSON.parse(text) : hasFallback ? fallback : null
}

// Unwrap the { error: { message } } envelope the admin API uses, so callers can
// show the server's own wording instead of a blob of JSON.
async function errorMessage(res) {
  const text = await res.text()
  try {
    return JSON.parse(text).error?.message || text || res.statusText
  } catch {
    return text || res.statusText
  }
}

// Parse the body regardless of status: endpoints that answer with
// { ok, message } use a 409 to signal failure but still carry a JSON body.
export const bodyAny = (res) => res.text().then((t) => (t ? JSON.parse(t) : null))
