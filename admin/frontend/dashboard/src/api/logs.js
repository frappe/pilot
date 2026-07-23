import { apiUrl, request } from './client'

export const logsApi = {
  list: () => request.get('logs').json(),
  read: (filename, lines) => request.get(`logs/${encodeURIComponent(filename)}`, { searchParams: { lines } }).json(),
  streamUrl: (filename) => apiUrl(`logs/${encodeURIComponent(filename)}/events`),
  downloadUrl: (filename) => apiUrl(`logs/${encodeURIComponent(filename)}/content`),
}
