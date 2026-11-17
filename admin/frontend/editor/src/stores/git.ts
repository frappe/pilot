import { defineStore } from 'pinia'

import { gitApi } from '@/api/git'
import type { BlameLine, LineChanges } from '@/codemirror/git'

interface FileStatus {
  path: string
  code: string
}

export const useGitStore = defineStore('git', {
  state: () => ({
    repo: false,
    branch: '',
    ahead: 0,
    behind: 0,
    hasUpstream: false,
    files: {} as Record<string, string>,
    staged: [] as FileStatus[],
    unstaged: [] as FileStatus[],
    loaded: false,
    blame: {} as Record<string, BlameLine[]>,
  }),
  getters: {
    count: (s) => Object.keys(s.files).length,
  },
  actions: {
    statusFor(path: string) {
      return this.files[path] || ''
    },
    async refresh() {
      try {
        const d = await gitApi.status()
        this.repo = !!d.repo
        this.branch = d.branch || ''
        this.ahead = d.ahead || 0
        this.behind = d.behind || 0
        this.hasUpstream = !!d.hasUpstream
        this.staged = d.staged || []
        this.unstaged = d.unstaged || []
        const map: Record<string, string> = {}
        for (const f of this.staged) map[f.path] = f.code
        for (const f of this.unstaged) map[f.path] = f.code
        this.files = map
        this.loaded = true
      } catch {
        this.repo = false
      }
    },
    async log(skip = 0, limit = 50) {
      return gitApi.log(skip, limit)
    },
    async commitInfo(sha: string) {
      return gitApi.commitInfo(sha)
    },
    async commitFileDiff(sha: string, path: string) {
      return gitApi.commitDiff(sha, path)
    },
    async stage(path: string) {
      const r = await gitApi.stage(path)
      await this.refresh()
      return r
    },
    async unstage(path: string) {
      const r = await gitApi.unstage(path)
      await this.refresh()
      return r
    },
    async discard(path: string) {
      const r = await gitApi.discard(path)
      await this.refresh()
      return r
    },
    async getBlame(path: string): Promise<BlameLine[]> {
      if (this.blame[path]) return this.blame[path]
      const d = await gitApi.blame(path)
      const lines: BlameLine[] = d.lines || []
      this.blame[path] = lines
      return lines
    },
    invalidateBlame(path: string) {
      delete this.blame[path]
    },
    async getDiff(path: string): Promise<LineChanges> {
      if (!this.repo) return { added: [], modified: [], deleted: [] }
      return gitApi.diff(path)
    },
    async branches() {
      return gitApi.branches()
    },
    async checkout(branch: string, create?: boolean) {
      const r = await gitApi.checkout(branch, create)
      await this.refresh()
      return r
    },
    async commit(message: string, all = true) {
      const r = await gitApi.commit(message, all)
      await this.refresh()
      return r
    },
    async push(force = false) {
      const r = await gitApi.push(force)
      await this.refresh()
      return r
    },
    async pull() {
      const r = await gitApi.pull()
      await this.refresh()
      return r
    },
  },
})
