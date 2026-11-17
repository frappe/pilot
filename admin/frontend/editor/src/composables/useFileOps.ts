import { dialog } from 'frappe-ui'

import { filesApi } from '@/api/files'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { parentOf, useTreeStore } from '@/stores/tree'
import { baseName } from '@/utils'

export const useFileOps = () => {
  const tree = useTreeStore()
  const editor = useEditorStore()
  const git = useGitStore()

  const newFile = (dir: string) => tree.startDraft(dir, 'file')

  const newFolder = (dir: string) => tree.startDraft(dir, 'dir')

  const rename = (path: string) => tree.startRename(path)

  const remove = (path: string, isDir: boolean) =>
    dialog.danger({
      title: 'Delete',
      message: `Delete ${baseName(path)}?`,
      onConfirm: async () => {
        await filesApi.del(path)

        if (isDir) editor.closeUnder(path)
        else editor.close(path)

        await tree.reload(parentOf(path))
        git.refresh()
      },
    })

  return { newFile, newFolder, rename, remove }
}
