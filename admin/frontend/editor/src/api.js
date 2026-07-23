async function req(method, url, body, extraHeaders = {}) {
  const headers = body ? { 'Content-Type': 'application/json', ...extraHeaders } : extraHeaders
  return fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
}

async function json(res) {
  const t = await res.text()
  return t ? JSON.parse(t) : null
}

async function okJSON(res) {
  if (!res.ok) throw new Error(await res.text())
  return json(res)
}

const jsonOr = (fallback) => (res) => (res.ok ? json(res) : fallback)

export const api = {
  tree(path = '') {
    return req('GET', `/api/tree?path=${encodeURIComponent(path)}`).then(okJSON)
  },
  read(path) {
    return req('GET', `/api/file?path=${encodeURIComponent(path)}`).then((res) => {
      if (res.status === 415) throw new Error('binary file')
      return okJSON(res)
    })
  },
  async save(path, content, etag) {
    const res = await req('PUT', `/api/file?path=${encodeURIComponent(path)}`, { content }, { 'If-Match': etag || '*' })
    if (res.status === 409) return { conflict: true, ...(await json(res)) }
    if (!res.ok) throw new Error(await res.text())
    return { conflict: false, ...(await json(res)) }
  },
  create(path, type) {
    return req('POST', '/api/create', { path, type }).then(okJSON)
  },
  rename(from, to) {
    return req('POST', '/api/rename', { from, to }).then(okJSON)
  },
  del(path) {
    return req('DELETE', `/api/delete?path=${encodeURIComponent(path)}`).then(okJSON)
  },
  files() {
    return req('GET', '/api/files').then(jsonOr([]))
  },
  gitStatus() {
    return req('GET', '/api/git/status').then(json)
  },
  gitDiff(path) {
    return req('GET', `/api/git/diff?path=${encodeURIComponent(path)}`).then(jsonOr({ added: [], modified: [], deleted: [] }))
  },
  gitShow(path) {
    return req('GET', `/api/git/show?path=${encodeURIComponent(path)}`).then(jsonOr({ content: '' }))
  },
  gitBranches() {
    return req('GET', '/api/git/branches').then(jsonOr({ current: '', branches: [] }))
  },
  gitCheckout(branch, create) {
    return req('POST', '/api/git/checkout', { branch, create: !!create }).then(json)
  },
  gitCommit(message, all) {
    return req('POST', '/api/git/commit', { message, all: !!all }).then(json)
  },
  gitFileDiff(path, staged) {
    return req('GET', `/api/git/filediff?path=${encodeURIComponent(path)}&staged=${staged ? 1 : 0}`).then(jsonOr({ diff: '' }))
  },
  gitLog(skip = 0, limit = 50) {
    return req('GET', `/api/git/log?skip=${skip}&limit=${limit}`).then(jsonOr({ repo: false, commits: [], more: false }))
  },
  gitCommitInfo(sha) {
    return req('GET', `/api/git/commit-info?sha=${encodeURIComponent(sha)}`).then(okJSON)
  },
  gitCommitDiff(sha, path) {
    return req('GET', `/api/git/commit-diff?sha=${encodeURIComponent(sha)}&path=${encodeURIComponent(path)}`).then(jsonOr({ diff: '' }))
  },
  gitStage(path) {
    return req('POST', '/api/git/stage', { path }).then(json)
  },
  gitUnstage(path) {
    return req('POST', '/api/git/unstage', { path }).then(json)
  },
  gitDiscard(path) {
    return req('POST', '/api/git/discard', { path }).then(json)
  },
  gitApply(patch, cached, reverse) {
    return req('POST', '/api/git/apply', { patch, cached: !!cached, reverse: !!reverse }).then(json)
  },
  gitPush(force) {
    return req('POST', '/api/git/push', { force: !!force }).then(json)
  },
  gitPull() {
    return req('POST', '/api/git/pull', {}).then(json)
  },
  async getState() {
    try {
      const res = await req('GET', '/api/state')
      return res.ok ? (await json(res)) || {} : {}
    } catch {
      return {}
    }
  },
  putState(state) {
    return fetch('/api/state', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state),
      keepalive: true,
    }).catch(() => {})
  },
}
