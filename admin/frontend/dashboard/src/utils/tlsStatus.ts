import type { TlsStatus } from '@/types/siteDomains'

export interface TlsBadge {
  label: string
  theme: 'red' | 'amber'
}

const days = (count: number) => `${count} ${count === 1 ? 'day' : 'days'}`

const expiryDate = (status: TlsStatus) =>
  status.expires_at
    ? new Date(status.expires_at).toLocaleDateString(undefined, { dateStyle: 'medium' })
    : ''

/** A badge for a certificate that needs attention; none while it is valid or pending. */
export const tlsBadge = (status: TlsStatus | null): TlsBadge | null => {
  if (status?.state === 'failed') return { label: 'Certificate failed', theme: 'red' }
  if (status?.state === 'expired') return { label: 'Certificate expired', theme: 'red' }
  if (status?.state === 'expiring')
    return { label: `Expires in ${days(status.days_left ?? 0)}`, theme: 'amber' }
  return null
}

export const tlsTooltip = (status: TlsStatus | null, tls: boolean, pending: boolean) => {
  if (status?.state === 'valid') return `Certificate valid until ${expiryDate(status)}`
  if (status?.state === 'expiring') return `Certificate expires on ${expiryDate(status)} and is not renewing`
  if (status?.state === 'expired') return `Certificate expired on ${expiryDate(status)}`
  if (status?.state === 'failed') return 'Certbot could not issue or renew the certificate'
  if (tls) return 'TLS active'
  return pending ? 'Certificate pending' : 'TLS inactive'
}

export const isTlsBroken = (status: TlsStatus | null) => tlsBadge(status) !== null
