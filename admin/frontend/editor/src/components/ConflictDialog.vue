<script setup lang="ts">
import { Dialog } from 'frappe-ui'
import { computed } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { baseName } from '@/utils'

const store = useEditorStore()
const name = computed(() => baseName(store.conflict?.path || ''))
</script>

<template>
  <Dialog
    :open="!!store.conflict"
    title="File changed on disk"
    :actions="[
      { label: 'Cancel', onClick: () => store.resolveConflict('cancel') },
      { label: 'Reload from disk', onClick: () => store.resolveConflict('reload') },
      {
        label: 'Override',
        variant: 'solid',
        theme: 'red',
        onClick: () => store.resolveConflict('override'),
      },
    ]"
    @update:open="(v: boolean) => !v && store.resolveConflict('cancel')"
  >
    <p class="text-sm text-ink-gray-7">
      <span class="font-medium text-ink-gray-9">{{ name }}</span>
      was modified by another process since you opened it. What would you like to do?
    </p>
  </Dialog>
</template>
