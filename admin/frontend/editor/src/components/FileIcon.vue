<template>
  <component :is="icon" class="shrink-0" />
</template>

<script setup>
import { computed } from 'vue'
import { fileIcon, FolderIcon, FolderOpenIcon } from '@/fileIcons'

const props = defineProps({
  name: { type: String, default: '' },
  dir: { type: Boolean, default: false },
  open: { type: Boolean, default: false },
})

const icon = computed(() =>
  props.dir ? (props.open ? FolderOpenIcon : FolderIcon) : fileIcon(props.name),
)
</script>
