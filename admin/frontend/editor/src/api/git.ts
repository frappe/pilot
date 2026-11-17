import { body, bodyAny, request } from '@/api/client'
import type { BlameLine, LineChanges } from '@/codemirror/git'

export interface FileStatus {
  path: string
  code: string
}

export interface GitStatus {
  repo: boolean
  branch?: string
  ahead?: number
  behind?: number
  hasUpstream?: boolean
  staged?: FileStatus[]
  unstaged?: FileStatus[]
}

export interface Commit {
  sha: string
  short: string
  author: string
  time: number
  subject: string
  refs: string
}

export interface CommitLog {
  repo: boolean
  commits: Commit[]
  more?: boolean
}

export interface CommitInfo {
  sha: string
  short: string
  author: string
  email: string
  time: number
  subject: string
  body: string
  files: FileStatus[]
}

export interface Branches {
  current: string
  branches: string[]
}

export interface CommitDiff {
  old: string
  new: string
}

export const gitApi = {
  status: () => request.get('git/status').then((r) => body<GitStatus>(r, { repo: false })),
  diff: (path: string) =>
    request
      .get('git/diff', { searchParams: { path } })
      .then((r) => body<Required<LineChanges>>(r, { added: [], modified: [], deleted: [] })),
  show: (path: string) =>
    request.get('git/show', { searchParams: { path } }).then((r) => body(r, { content: '' })),
  blame: (path: string) =>
    request
      .get('git/blame', { searchParams: { path } })
      .then((r) => body<{ lines: BlameLine[] }>(r, { lines: [] })),
  branches: () =>
    request.get('git/branches').then((r) => body<Branches>(r, { current: '', branches: [] })),
  log: (skip = 0, limit = 50) =>
    request
      .get('git/log', { searchParams: { skip, limit } })
      .then((r) => body<CommitLog>(r, { repo: false, commits: [], more: false })),
  commitInfo: (sha: string) =>
    request.get('git/commit-info', { searchParams: { sha } }).then((r) => body<CommitInfo>(r)),
  commitDiff: (sha: string, path: string) =>
    request
      .get('git/commit-diff', { searchParams: { sha, path } })
      .then((r) => body<CommitDiff>(r, { old: '', new: '' })),

  checkout: (branch: string, create?: boolean) =>
    request.post('git/checkout', { json: { branch, create: !!create } }).then(bodyAny),
  commit: (message: string, all?: boolean) =>
    request.post('git/commit', { json: { message, all: !!all } }).then(bodyAny),
  stage: (path: string) => request.post('git/stage', { json: { path } }).then(bodyAny),
  unstage: (path: string) => request.post('git/unstage', { json: { path } }).then(bodyAny),
  discard: (path: string) => request.post('git/discard', { json: { path } }).then(bodyAny),
  push: (force?: boolean) => request.post('git/push', { json: { force: !!force } }).then(bodyAny),
  pull: () => request.post('git/pull', { json: {} }).then(bodyAny),
}
