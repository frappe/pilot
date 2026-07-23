import { defineStore } from 'pinia'
import { api } from '@/api'
import { useGitStore } from '@/stores/git'
import { baseName } from '@/utils'

const autosaveTimers = new Map()

const MIN_FONT = 9
const MAX_FONT = 24
const clampFont = (n) => Math.min(MAX_FONT, Math.max(MIN_FONT, n || 13))

export const useEditorStore = defineStore('editor', {
  state: () => ({
    tabs: [],
    activePath: null,
    // Bumped on every open() call (even re-opening the active file) so the UI
    // can react to "a file was opened" regardless of whether the path changed.
    openTick: 0,
    conflict: null,
    reveal: null,
    diff: null,
    // Set to { sha } while viewing a past commit's changes.
    commit: null,
    cursors: {},
    autosave: localStorage.getItem('hatch-autosave') !== '0',
    blame: localStorage.getItem('hatch-blame') === '1',
    fontSize: clampFont(Number(localStorage.getItem('hatch-fontsize'))),
    // On touch devices the editor opens read-only so tapping to scroll doesn't
    // raise the keyboard; the user opts into editing from the mobile menu.
    mobileEdit: localStorage.getItem('hatch-mobileedit') === '1',
  }),
  getters: {
    active: (s) => s.tabs.find((t) => t.path === s.activePath) || null,
  },
  actions: {
    async open(path, line, col) {
      const existing = this.tabs.find((t) => t.path === path)
      if (!existing) {
        const data = await api.read(path)
        this.tabs.push({
          path,
          name: baseName(path),
          content: data.content,
          saved: data.content,
          etag: data.etag,
          dirty: false,
        })
      }
      this.activePath = path
      this.openTick++
      if (line) this.reveal = { path, line, col: col || 1, ts: Date.now() }
    },
    async refreshTab(path) {
      const t = this.tabs.find((t) => t.path === path)
      if (!t) return
      const data = await api.read(path)
      t.content = data.content
      t.saved = data.content
      t.etag = data.etag
      t.dirty = false
      t.external = (t.external || 0) + 1
    },
    setContent(path, content) {
      const t = this.tabs.find((t) => t.path === path)
      if (!t) return
      t.content = content
      t.dirty = content !== t.saved
      if (this.autosave && t.dirty) {
        clearTimeout(autosaveTimers.get(path))
        autosaveTimers.set(
          path,
          setTimeout(() => this.save(path).catch(() => {}), 600),
        )
      }
    },
    toggleAutosave() {
      this.autosave = !this.autosave
      localStorage.setItem('hatch-autosave', this.autosave ? '1' : '0')
      if (this.autosave) {
        for (const t of this.tabs) if (t.dirty) this.save(t.path).catch(() => {})
      }
    },
    toggleBlame() {
      this.blame = !this.blame
      localStorage.setItem('hatch-blame', this.blame ? '1' : '0')
    },
    setFontSize(n) {
      this.fontSize = clampFont(n)
      localStorage.setItem('hatch-fontsize', String(this.fontSize))
    },
    bumpFont(delta) {
      this.setFontSize(this.fontSize + delta)
    },
    toggleMobileEdit() {
      this.mobileEdit = !this.mobileEdit
      localStorage.setItem('hatch-mobileedit', this.mobileEdit ? '1' : '0')
    },
    async save(path) {
      const t = this.tabs.find((t) => t.path === path)
      if (!t) return
      // Serialize saves per file. Overlapping saves (autosave + manual, or
      // save-on-close) share a stale etag, so the second one 409s against the
      // content the first just wrote. Coalesce instead.
      if (t.saving) {
        t.saveAgain = true
        return
      }
      t.saving = true
      try {
        const sent = t.content
        const r = await api.save(t.path, sent, t.etag)
        if (r.conflict) {
          this.conflict = { path: t.path, disk: r.content, etag: r.etag }
          return
        }
        t.etag = r.etag
        t.saved = sent
        t.dirty = t.content !== sent
        const git = useGitStore()
        git.invalidateBlame(t.path)
        git.refresh()
      } finally {
        t.saving = false
        if (t.saveAgain) {
          t.saveAgain = false
          if (t.dirty) this.save(path)
        }
      }
    },
    async resolveConflict(action) {
      const c = this.conflict
      this.conflict = null
      if (!c) return
      const t = this.tabs.find((t) => t.path === c.path)
      if (!t) return
      if (action === 'override') {
        const r = await api.save(t.path, t.content, '*')
        t.etag = r.etag
        t.saved = t.content
        t.dirty = false
      } else if (action === 'reload') {
        t.content = c.disk
        t.saved = c.disk
        t.etag = c.etag
        t.dirty = false
        t.external = (t.external || 0) + 1
      }
    },
    close(path) {
      const i = this.tabs.findIndex((t) => t.path === path)
      if (i === -1) return
      this.tabs.splice(i, 1)
      if (this.activePath === path) {
        const next = this.tabs[i] || this.tabs[i - 1]
        this.activePath = next ? next.path : null
      }
    },
    closeOthers(path) {
      for (const t of [...this.tabs]) {
        if (t.path !== path) this.close(t.path)
      }
      this.activePath = path
    },
    closeAll() {
      for (const t of [...this.tabs]) this.close(t.path)
    },
    closeSaved() {
      for (const t of [...this.tabs]) {
        if (!t.dirty) this.close(t.path)
      }
    },
    openDiff(path, staged = false) {
      this.diff = { path, staged: !!staged }
      this.commit = null
    },
    closeDiff() {
      this.diff = null
    },
    openCommit(sha) {
      this.commit = { sha }
      this.diff = null
    },
    closeCommit() {
      this.commit = null
    },
    renamePath(from, to) {
      const t = this.tabs.find((t) => t.path === from)
      if (t) {
        t.path = to
        t.name = baseName(to)
        if (this.activePath === from) this.activePath = to
      }
    },
    closeUnder(prefix) {
      const pfx = prefix + '/'
      this.tabs
        .filter((t) => t.path === prefix || t.path.startsWith(pfx))
        .forEach((t) => this.close(t.path))
    },
    setCursor(path, line, col) {
      if (path) this.cursors[path] = { line, col }
    },
    snapshot() {
      const cursors = {}
      for (const t of this.tabs) if (this.cursors[t.path]) cursors[t.path] = this.cursors[t.path]
      return { tabs: this.tabs.map((t) => t.path), active: this.activePath, cursors, autosave: this.autosave, blame: this.blame }
    },
    async restore(snap) {
      if (!snap || !Array.isArray(snap.tabs) || !snap.tabs.length) return
      if (typeof snap.autosave === 'boolean') this.autosave = snap.autosave
      if (typeof snap.blame === 'boolean') this.blame = snap.blame
      this.cursors = snap.cursors || {}
      for (const p of snap.tabs) {
        try {
          await this.open(p)
        } catch {
          /* file may have been removed/renamed since */
        }
      }
      const act =
        snap.active && this.tabs.some((t) => t.path === snap.active)
          ? snap.active
          : this.tabs[0]?.path || null
      if (act) {
        const c = this.cursors[act]
        await this.open(act, c?.line, c?.col).catch(() => {})
      }
    },
  },
})
