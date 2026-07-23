import { api } from '@/api'
import { useTreeStore, parentOf } from '@/stores/tree'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { baseName } from '@/utils'

export function useFileOps() {
  const tree = useTreeStore()
  const editor = useEditorStore()
  const git = useGitStore()

  function newFile(dir) {
    tree.startDraft(dir, 'file')
  }
  function newFolder(dir) {
    tree.startDraft(dir, 'dir')
  }
  function rename(path) {
    tree.startRename(path)
  }
  async function remove(path, isDir) {
    if (!confirm(`Delete ${baseName(path)}?`)) return
    await api.del(path)
    if (isDir) editor.closeUnder(path)
    else editor.close(path)
    await tree.reload(parentOf(path))
    git.refresh()
  }

  return { newFile, newFolder, rename, remove }
}
