import { defineStore } from 'pinia'

import { type Entry, type EntryType, filesApi } from '@/api/files'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { baseName } from '@/utils'

export interface TreeNode {
  expanded: boolean
  loading: boolean
  loaded: boolean
  children: Entry[]
}

interface Draft {
  parent: string
  type: EntryType
}

export const parentOf = (p: string) => {
  const i = p.lastIndexOf('/')

  return i === -1 ? '' : p.slice(0, i)
}

const join = (dir: string, name: string) => (dir ? `${dir}/${name}` : name)

export const useTreeStore = defineStore('tree', {
  state: () => ({
    nodes: {} as Record<string, TreeNode>,
    draft: null as Draft | null,
    editing: null as string | null,
    dragPath: null as string | null,
    focusPath: null as string | null,
  }),
  actions: {
    node(path: string) {
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
    async load(path: string) {
      const n = this.node(path)

      n.loading = true

      try {
        n.children = await filesApi.tree(path)
        n.loaded = true
      } finally {
        n.loading = false
      }
    },
    async toggle(path: string) {
      const n = this.node(path)

      n.expanded = !n.expanded

      if (n.expanded && !n.loaded) await this.load(path)
    },
    async reload(path: string) {
      const n = this.node(path)

      if (n.loaded || path === '') await this.load(path)
    },
    async revealDir(path: string) {
      const n = this.node(path)

      n.expanded = true
      await this.load(path)
    },
    async reveal(path: string) {
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

    startDraft(parent: string, type: EntryType) {
      this.editing = null
      this.draft = { parent, type }

      if (parent) this.revealDir(parent)
    },
    cancelDraft() {
      this.draft = null
    },
    async commitDraft(input: string) {
      const d = this.draft
      const name = (input || '').trim()

      this.draft = null

      if (!d || !name) return

      const path = join(d.parent, name)

      await filesApi.create(path, d.type)
      await this.reload(d.parent)
      useGitStore().refresh()

      if (d.type === 'file')
        useEditorStore()
          .open(path)
          .catch(() => {})
    },

    startRename(path: string) {
      this.draft = null
      this.editing = path
    },
    cancelRename() {
      this.editing = null
    },
    async commitRename(path: string, input: string) {
      const name = (input || '').trim()

      this.editing = null

      if (!name || name === baseName(path)) return

      const to = join(parentOf(path), name)

      await filesApi.rename(path, to)
      useEditorStore().renamePath(path, to)
      await this.reload(parentOf(path))
      useGitStore().refresh()
    },

    async move(from: string | null, toDir: string) {
      if (!from) return

      if (parentOf(from) === toDir) return

      const to = join(toDir, baseName(from))

      if (from === to || to.startsWith(`${from}/`)) return

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
