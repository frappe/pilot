<template>
  <Dialog
    :modelValue="!!store.conflict"
    @update:modelValue="(v) => !v && store.resolveConflict('cancel')"
    :options="{ title: 'File changed on disk' }"
  >
    <template #body-content>
      <p class="text-sm text-ink-gray-7">
        <span class="font-medium text-ink-gray-9">{{ name }}</span> was modified by
        another process since you opened it. What would you like to do?
      </p>
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" @click="store.resolveConflict('cancel')" />
        <Button label="Reload from disk" @click="store.resolveConflict('reload')" />
        <Button
          variant="solid"
          theme="red"
          label="Override"
          @click="store.resolveConflict('override')"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed } from 'vue'
import { Dialog, Button } from 'frappe-ui'
import { useEditorStore } from '@/stores/editor'
import { baseName } from '@/utils'

const store = useEditorStore()
const name = computed(() => baseName(store.conflict?.path || ''))
</script>
