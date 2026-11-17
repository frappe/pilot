<script setup lang="ts">
import { computed } from 'vue'

import { FolderIcon, FolderOpenIcon, fileIcon } from '@/fileIcons'

const props = withDefaults(defineProps<{ name?: string; dir?: boolean; open?: boolean }>(), {
  name: '',
  dir: false,
  open: false,
})

const icon = computed(() =>
  props.dir ? (props.open ? FolderOpenIcon : FolderIcon) : fileIcon(props.name),
)
</script>

<template>
  <component :is="icon" class="shrink-0" />
</template>
