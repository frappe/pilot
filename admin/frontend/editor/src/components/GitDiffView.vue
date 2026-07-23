<template>
  <div class="flex h-full flex-col bg-surface-white">
    <div class="flex items-center gap-2 border-b border-outline-gray-1 px-3 py-2 sm:py-1.5">
      <FileIcon :name="baseName(path)" class="h-4 w-4 shrink-0" />
      <span class="truncate text-sm font-medium text-ink-gray-8">{{ baseName(path) }}</span>
      <span class="truncate text-2xs text-ink-gray-4">{{ dirName(path) }}</span>

      <div class="ml-auto flex items-center gap-1">
        <button
          class="rounded p-2 text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8 sm:p-1"
          title="Open file"
          @click="openFile"
        >
          <span class="lucide-external-link h-5 w-5 text-current sm:h-3.5 sm:w-3.5"></span>
        </button>
        <button
          class="rounded p-2 text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8 sm:p-1"
          title="Close diff"
          @click="editor.closeDiff()"
        >
          <span class="lucide-x h-5 w-5 text-current sm:h-4 sm:w-4"></span>
        </button>
      </div>
    </div>

    <div class="min-h-0 flex-1">
      <div v-if="loading" class="p-4 text-xs text-ink-gray-4">loading…</div>
      <DiffEditor v-else :original="original" :modified="modified" :path="path" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import FileIcon from '@/components/FileIcon.vue'
import DiffEditor from '@/components/DiffEditor.vue'
import { filesApi } from '@/api/files'
import { gitApi } from '@/api/git'
import { useEditorStore } from '@/stores/editor'
import { baseName, dirName } from '@/utils'

const editor = useEditorStore()

const loading = ref(false)
const original = ref('')
const modified = ref('')

const path = computed(() => editor.diff?.path || '')

async function load() {
  if (!path.value) return
  loading.value = true
  const [head, file] = await Promise.all([
    gitApi.show(path.value),
    filesApi.read(path.value).catch(() => ({ content: '' })),
  ])
  original.value = head.content || ''
  modified.value = file.content || ''
  loading.value = false
}

watch(() => editor.diff?.path, () => editor.diff && load(), { immediate: true })

function openFile() {
  const p = path.value
  editor.closeDiff()
  editor.open(p).catch(() => {})
}
</script>
