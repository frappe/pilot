import { apiUrl, request } from '@/api/client'
import type { LogFile, LogTail } from '@/types/logs'

// Keep the slash of `workload/<name>`, so the path reads like the folder on disk.
const logPath = (filename: string) => `logs/${encodeURIComponent(filename).replaceAll('%2F', '/')}`

export const logsApi = {
  list: (): Promise<LogFile[]> => request.get('logs').json(),

  read: (filename: string, lines: number): Promise<LogTail> =>
    request.get(logPath(filename), { searchParams: { lines } }).json(),

  streamUrl: (filename: string) => apiUrl(`${logPath(filename)}/events`),
  downloadUrl: (filename: string) => apiUrl(`${logPath(filename)}/content`),
}
