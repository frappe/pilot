import ky from 'ky'

import { APP } from '@/scope'

export interface GitResult {
  ok: boolean
  message: string
}

let redirectingToLogin = false

const redirectToLogin = () => {
  if (redirectingToLogin) return

  redirectingToLogin = true

  const back = encodeURIComponent(location.pathname + location.search)

  location.assign(`/login?redirect=${back}`)
}

const isLoginRedirect = (response: Response) => {
  if (!response.redirected) return false

  const type = response.headers.get('content-type') || ''

  return type.includes('text/html')
}

// Every editor endpoint is scoped to one app via ?app=. ky merges these instance
// searchParams onto each request, so we never rebuild the Request ourselves —
// `new Request(url, request)` drops the body on some browsers, which silently
// turned writes (save, discard, commit) into empty payloads.
export const request = ky.create({
  prefix: '/api/v1/editor',
  searchParams: { app: APP },
  throwHttpErrors: false,
  timeout: 60_000,
  hooks: {
    afterResponse: [
      ({ response }) => {
        if (response.status === 401 || isLoginRedirect(response)) redirectToLogin()
      },
    ],
  },
})

// Unwrap the { error: { message } } envelope the admin API uses, so callers can
// show the server's own wording instead of a blob of JSON.
const errorMessage = async (res: Response) => {
  const text = await res.text()

  try {
    return JSON.parse(text).error?.message || text || res.statusText
  } catch {
    return text || res.statusText
  }
}

// Parse a JSON body. With a fallback, returns it on any failed request; without
// one, throws the server's message. Empty bodies yield the fallback or null.
export const body = async <T>(res: Response, ...fallback: [] | [T]): Promise<T> => {
  const hasFallback = fallback.length > 0

  if (!res.ok) {
    if (hasFallback) return fallback[0] as T

    throw new Error(await errorMessage(res))
  }

  const text = await res.text()

  return text ? JSON.parse(text) : ((hasFallback ? fallback[0] : null) as T)
}

// Parse the body regardless of status: endpoints that answer with
// { ok, message } use a 409 to signal failure but still carry a JSON body.
export const bodyAny = (res: Response): Promise<GitResult | null> =>
  res.text().then((t) => (t ? JSON.parse(t) : null))
