import { defineStore } from 'pinia'
import { api } from '@/api'

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
        const d = await api.gitStatus()
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
    async fileDiff(path, staged) {
      return api.gitFileDiff(path, staged)
    },
    async log(skip = 0, limit = 50) {
      return api.gitLog(skip, limit)
    },
    async commitInfo(sha) {
      return api.gitCommitInfo(sha)
    },
    async commitFileDiff(sha, path) {
      return api.gitCommitDiff(sha, path)
    },
    async stage(path) {
      const r = await api.gitStage(path)
      await this.refresh()
      return r
    },
    async unstage(path) {
      const r = await api.gitUnstage(path)
      await this.refresh()
      return r
    },
    async discard(path) {
      const r = await api.gitDiscard(path)
      await this.refresh()
      return r
    },
    async apply(patch, cached, reverse) {
      const r = await api.gitApply(patch, cached, reverse)
      await this.refresh()
      return r
    },
    async getBlame(path) {
      if (this.blame[path]) return this.blame[path]
      const res = await fetch(`/api/git/blame?path=${encodeURIComponent(path)}`)
      const d = await res.json().catch(() => ({}))
      const lines = d.lines || []
      this.blame[path] = lines
      return lines
    },
    invalidateBlame(path) {
      delete this.blame[path]
    },
    async getDiff(path) {
      if (!this.repo) return { added: [], modified: [], deleted: [] }
      return api.gitDiff(path)
    },
    async branches() {
      return api.gitBranches()
    },
    async checkout(branch, create) {
      const r = await api.gitCheckout(branch, create)
      await this.refresh()
      return r
    },
    async commit(message, all = true) {
      const r = await api.gitCommit(message, all)
      await this.refresh()
      return r
    },
    async push(force = false) {
      const r = await api.gitPush(force)
      await this.refresh()
      return r
    },
    async pull() {
      const r = await api.gitPull()
      await this.refresh()
      return r
    },
  },
})
