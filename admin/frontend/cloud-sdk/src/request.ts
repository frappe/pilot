const methodPrefix = 'frappe.integrations.frappe_providers.cloud_settings'

type ErrorBody = {
  exc_type?: string
  _server_messages?: string
}

const parseServerMessages = (raw?: string): string[] => {
  if (!raw) return []

  try {
    const messages: unknown = JSON.parse(raw)
    if (!Array.isArray(messages)) return []

    return messages.flatMap((item: unknown) => {
      const entry = typeof item === 'string' ? JSON.parse(item) : item
      const message = entry?.message
      return typeof message === 'string' ? [message.replace(/<[^>]*>/g, '')] : []
    })
  } catch {
    return []
  }
}

const fallbackMessage = (status: number, excType?: string) => {
  if (status === 0) return 'Could not reach Cloud Settings. Please try again.'
  if (status === 403) return "You don't have permission to do this."
  if (excType) return `${excType}. Please try again.`

  return 'Something went wrong. Please try again.'
}

export class CloudSettingsError extends Error {
  status: number
  excType: string
  serverMessages: string[]

  constructor(status: number, body: ErrorBody) {
    const serverMessages = parseServerMessages(body._server_messages)

    super(serverMessages.join('. ') || fallbackMessage(status, body.exc_type))

    this.name = 'CloudSettingsError'
    this.status = status
    this.excType = body.exc_type || ''
    this.serverMessages = serverMessages
  }
}

export const isMigrationConflict = (exception: unknown) =>
  exception instanceof CloudSettingsError && exception.excType === 'CloudMigrationConflictError'

const encodeValue = (value: unknown) => {
  if (value == null) return ''

  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

const csrfToken = () => {
  const page = globalThis as { csrf_token?: string; frappe?: { csrf_token?: string } }

  return page.frappe?.csrf_token || page.csrf_token || ''
}

export const call = async <T = unknown>(
  method: string,
  args: Record<string, unknown> = {},
  type: 'GET' | 'POST' = 'POST',
): Promise<T> => {
  const url = `/api/method/${methodPrefix}.${method}`
  const params = new URLSearchParams()

  for (const [key, value] of Object.entries(args)) params.append(key, encodeValue(value))

  let response: Response
  try {
    response = await fetch(type === 'GET' ? `${url}?${params}` : url, {
      method: type,
      body: type === 'POST' ? params : undefined,
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'X-Frappe-CSRF-Token': csrfToken() },
      signal: AbortSignal.timeout(30_000),
    })
  } catch (cause) {
    throw new CloudSettingsError(0, { exc_type: cause instanceof Error ? cause.name : 'NetworkError' })
  }

  const parsed: unknown = await response.json().catch(() => ({}))
  const body = parsed && typeof parsed === 'object' ? parsed : {}

  if (!response.ok) throw new CloudSettingsError(response.status, body)
  if (!Object.hasOwn(body, 'message')) {
    throw new CloudSettingsError(response.status, { exc_type: 'InvalidResponse' })
  }

  return (body as { message: T }).message
}
