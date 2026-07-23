import { defineStore } from 'pinia'
import { gitApi } from '@/api/git'

export const useGitStore = defineStore('git', {
  state: () => ({
    repo: false,
    branch: '',
    ahead: 0,
    behind: 0,
    hasUpstream: false,
    files: {},
    staged: [],
    unstaged: [],
    loaded: false,
    blame: {},
  }),
  getters: {
    count: (s) => Object.keys(s.files).length,
  },
  actions: {
    statusFor(path) {
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
        const map = {}
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
    async commitInfo(sha) {
      return gitApi.commitInfo(sha)
    },
    async commitFileDiff(sha, path) {
      return gitApi.commitDiff(sha, path)
    },
    async stage(path) {
      const r = await gitApi.stage(path)
      await this.refresh()
      return r
    },
    async unstage(path) {
      const r = await gitApi.unstage(path)
      await this.refresh()
      return r
    },
    async discard(path) {
      const r = await gitApi.discard(path)
      await this.refresh()
      return r
    },
    async getBlame(path) {
      if (this.blame[path]) return this.blame[path]
      const d = await gitApi.blame(path)
      const lines = d.lines || []
      this.blame[path] = lines
      return lines
    },
    invalidateBlame(path) {
      delete this.blame[path]
    },
    async getDiff(path) {
      if (!this.repo) return { added: [], modified: [], deleted: [] }
      return gitApi.diff(path)
    },
    async branches() {
      return gitApi.branches()
    },
    async checkout(branch, create) {
      const r = await gitApi.checkout(branch, create)
      await this.refresh()
      return r
    },
    async commit(message, all = true) {
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
