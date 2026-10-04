import ky from 'ky'
import type { ErrorResponse } from '@/types/common'
import { isSignedOut, reportSignedOut } from '../composables/auth/useSignedOut.ts'

export const API_V1_PREFIX = '/api/v1'

export const apiUrl = (path = '', origin = ''): string => {
  const suffix = path ? `/${String(path).replace(/^\/+/, '')}` : ''
  return `${origin}${API_V1_PREFIX}${suffix}`
}

const errorOf = (payload: unknown): unknown => {
  if (typeof payload !== 'object' || payload === null || !('error' in payload)) return undefined
  return payload.error
}

export const hasApiError = (payload: unknown): boolean => Boolean(errorOf(payload))

export const apiErrorMessage = (payload: unknown, fallback = 'Request failed.'): string => {
  const error = errorOf(payload)
  if (typeof error === 'string' && error) return error
  if (typeof error === 'object' && error !== null && 'message' in error) {
    const message = error.message
    if (typeof message === 'string' && message) return message
  }
  return fallback
}

export const unwrap = async <T>(parsed: Promise<T>): Promise<T> => {
  const data = await parsed
  if (hasApiError(data)) {
    if (isSignedOut()) return new Promise<T>(() => {})
    throw new Error(apiErrorMessage(data))
  }
  return data
}

export const isSessionExpired = async (response: Response) => {
  if (response?.status !== 401) return false
  try {
    const body: Partial<ErrorResponse> = await response.clone().json()
    return body?.error?.code === 'authentication_required'
  } catch {
    return false
  }
}

export const request = ky.create({
  prefix: API_V1_PREFIX,
  throwHttpErrors: false,
  timeout: 60_000,
  hooks: {
    afterResponse: [
      async ({ response }) => {
        if (await isSessionExpired(response)) reportSignedOut()
      },
    ],
  },
})
