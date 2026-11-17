import { body, request } from '@/api/client'

export type EntryType = 'file' | 'dir'

export interface Entry {
  name: string
  path: string
  type: EntryType
  size: number
  mtime: number
  ignored?: boolean
}

export interface FileData {
  path: string
  content: string
  etag: string
}

export type SaveResult =
  | { conflict: false; etag: string }
  | { conflict: true; path: string; content: string; etag: string }

export const filesApi = {
  tree: (path = '') =>
    request.get('tree', { searchParams: { path } }).then((r) => body<Entry[]>(r)),
  list: () => request.get('files').then((r) => body<string[]>(r, [])),
  create: (path: string, type: EntryType) =>
    request.post('create', { json: { path, type } }).then((r) => body<{ path: string }>(r)),
  rename: (from: string, to: string) =>
    request.post('rename', { json: { from, to } }).then((r) => body<{ path: string }>(r)),
  del: (path: string) =>
    request.delete('delete', { searchParams: { path } }).then((r) => body<{ ok: boolean }>(r)),

  read: async (path: string) => {
    const res = await request.get('file', { searchParams: { path } })

    if (res.status === 415) throw new Error('binary file')

    return body<FileData>(res)
  },

  save: async (path: string, content: string, etag: string): Promise<SaveResult> => {
    const res = await request.put('file', {
      searchParams: { path },
      headers: { 'If-Match': etag || '*' },
      json: { content },
    })

    if (res.status === 409) return { conflict: true, ...(await res.json<FileData>()) }

    if (!res.ok) throw new Error((await res.text()) || 'Save failed')

    return { conflict: false, ...(await res.json<{ etag: string }>()) }
  },
}
