import { request, body } from './client'

export const filesApi = {
  tree: (path = '') => request.get('tree', { searchParams: { path } }).then((r) => body(r)),
  list: () => request.get('files').then((r) => body(r, [])),
  create: (path, type) => request.post('create', { json: { path, type } }).then((r) => body(r)),
  rename: (from, to) => request.post('rename', { json: { from, to } }).then((r) => body(r)),
  del: (path) => request.delete('delete', { searchParams: { path } }).then((r) => body(r)),

  async read(path) {
    const res = await request.get('file', { searchParams: { path } })
    if (res.status === 415) throw new Error('binary file')
    return body(res)
  },

  async save(path, content, etag) {
    const res = await request.put('file', {
      searchParams: { path },
      headers: { 'If-Match': etag || '*' },
      json: { content },
    })
    if (res.status === 409) return { conflict: true, ...(await res.json()) }
    if (!res.ok) throw new Error((await res.text()) || 'Save failed')
    return { conflict: false, ...(await res.json()) }
  },
}
