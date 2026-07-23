import { confirmDialog } from 'frappe-ui'
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
  function remove(path, isDir) {
    confirmDialog({
      title: 'Delete',
      message: `Delete ${baseName(path)}?`,
      onConfirm: async ({ hideDialog }) => {
        hideDialog()
        await api.del(path)
        if (isDir) editor.closeUnder(path)
        else editor.close(path)
        await tree.reload(parentOf(path))
        git.refresh()
      },
    })
  }

  return { newFile, newFolder, rename, remove }
}
