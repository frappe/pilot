import { apiUrl, request } from '@/api/client'
import type { LogFile, LogTail } from '@/types/logs'

export const logsApi = {
  list: (): Promise<LogFile[]> => request.get('logs').json(),

  read: (filename: string, lines: number): Promise<LogTail> =>
    request.get(`logs/${encodeURIComponent(filename)}`, { searchParams: { lines } }).json(),

  streamUrl: (filename: string) => apiUrl(`logs/${encodeURIComponent(filename)}/events`),
  downloadUrl: (filename: string) => apiUrl(`logs/${encodeURIComponent(filename)}/content`),
}
