import { defineStore } from 'pinia'
import { filesApi } from '@/api/files'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { baseName } from '@/utils'

export function parentOf(p) {
  const i = p.lastIndexOf('/')
  return i === -1 ? '' : p.slice(0, i)
}

function join(dir, name) {
  return dir ? `${dir}/${name}` : name
}

export const useTreeStore = defineStore('tree', {
  state: () => ({
    nodes: {},
    draft: null,
    editing: null,
    dragPath: null,
    focusPath: null,
  }),
  actions: {
    node(path) {
      if (!this.nodes[path]) {
        this.nodes[path] = {
          expanded: false,
          loading: false,
          loaded: false,
          children: [],
        }
      }
      return this.nodes[path]
    },
    async load(path) {
      const n = this.node(path)
      n.loading = true
      try {
        n.children = await filesApi.tree(path)
        n.loaded = true
      } finally {
        n.loading = false
      }
    },
    async toggle(path) {
      const n = this.node(path)
      n.expanded = !n.expanded
      if (n.expanded && !n.loaded) await this.load(path)
    },
    async reload(path) {
      const n = this.node(path)
      if (n.loaded || path === '') await this.load(path)
    },
    async revealDir(path) {
      const n = this.node(path)
      n.expanded = true
      await this.load(path)
    },
    async reveal(path) {
      if (!path || path === '') return
      const parts = path.split('/')
      let acc = ''
      for (let i = 0; i < parts.length - 1; i++) {
        acc = acc ? `${acc}/${parts[i]}` : parts[i]
        const n = this.node(acc)
        n.expanded = true
        if (!n.loaded) await this.load(acc)
      }
    },

    startDraft(parent, type) {
      this.editing = null
      this.draft = { parent, type }
      if (parent) this.revealDir(parent)
    },
    cancelDraft() {
      this.draft = null
    },
    async commitDraft(name) {
      const d = this.draft
      this.draft = null
      name = (name || '').trim()
      if (!d || !name) return
      const path = join(d.parent, name)
      await filesApi.create(path, d.type)
      await this.reload(d.parent)
      useGitStore().refresh()
      if (d.type === 'file') useEditorStore().open(path).catch(() => {})
    },

    startRename(path) {
      this.draft = null
      this.editing = path
    },
    cancelRename() {
      this.editing = null
    },
    async commitRename(path, name) {
      this.editing = null
      name = (name || '').trim()
      if (!name || name === baseName(path)) return
      const to = join(parentOf(path), name)
      await filesApi.rename(path, to)
      useEditorStore().renamePath(path, to)
      await this.reload(parentOf(path))
      useGitStore().refresh()
    },

    async move(from, toDir) {
      if (!from) return
      if (parentOf(from) === toDir) return
      const to = join(toDir, baseName(from))
      if (from === to || to.startsWith(from + '/')) return
      await filesApi.rename(from, to)
      useEditorStore().renamePath(from, to)
      await this.reload(parentOf(from))
      const n = this.node(toDir)
      if (toDir && !n.expanded) await this.toggle(toDir)
      else await this.reload(toDir)
      useGitStore().refresh()
    },
  },
})
