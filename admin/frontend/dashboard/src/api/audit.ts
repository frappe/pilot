import { apiUrl, request, unwrap } from '@/api/client'
import type { AuditPage } from '@/types/settings'

export const auditApi = {
  list: (params: Record<string, string | number>): Promise<AuditPage> =>
    unwrap(request.get('audit-events', { searchParams: params }).json()),
  exportUrl: (params: Record<string, string>): string =>
    `${apiUrl('audit-events/export')}?${new URLSearchParams(params)}`,
}
