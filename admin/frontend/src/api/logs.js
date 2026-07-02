import { request } from './client'

export const logsApi = {
  list: () => request.get('logs/').json(),
  read: (filename, lines) => request.get(`logs/${encodeURIComponent(filename)}`, { searchParams: { lines } }).json(),
  streamUrl: (filename) => `/api/logs/${encodeURIComponent(filename)}/stream`,
  downloadUrl: (filename) => `/api/logs/${encodeURIComponent(filename)}/download`,
}
