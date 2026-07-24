import { request, body, bodyAny } from './client'

export const gitApi = {
  status: () => request.get('git/status').then((r) => body(r, { repo: false })),
  diff: (path) =>
    request.get('git/diff', { searchParams: { path } }).then((r) => body(r, { added: [], modified: [], deleted: [] })),
  show: (path) => request.get('git/show', { searchParams: { path } }).then((r) => body(r, { content: '' })),
  blame: (path) => request.get('git/blame', { searchParams: { path } }).then((r) => body(r, { lines: [] })),
  branches: () => request.get('git/branches').then((r) => body(r, { current: '', branches: [] })),
  log: (skip = 0, limit = 50) =>
    request.get('git/log', { searchParams: { skip, limit } }).then((r) => body(r, { repo: false, commits: [], more: false })),
  commitInfo: (sha) => request.get('git/commit-info', { searchParams: { sha } }).then((r) => body(r)),
  commitDiff: (sha, path) =>
    request.get('git/commit-diff', { searchParams: { sha, path } }).then((r) => body(r, { old: '', new: '' })),

  checkout: (branch, create) => request.post('git/checkout', { json: { branch, create: !!create } }).then(bodyAny),
  commit: (message, all) => request.post('git/commit', { json: { message, all: !!all } }).then(bodyAny),
  stage: (path) => request.post('git/stage', { json: { path } }).then(bodyAny),
  unstage: (path) => request.post('git/unstage', { json: { path } }).then(bodyAny),
  discard: (path) => request.post('git/discard', { json: { path } }).then(bodyAny),
  push: (force) => request.post('git/push', { json: { force: !!force } }).then(bodyAny),
  pull: () => request.post('git/pull', { json: {} }).then(bodyAny),
}
