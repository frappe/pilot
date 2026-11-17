<script setup lang="ts">
import { onMounted, ref } from 'vue'

import FileIcon from '@/components/FileIcon.vue'

const props = withDefaults(defineProps<{ type?: string; initial?: string }>(), {
  type: 'file',
  initial: '',
})

const emit = defineEmits<{ commit: [name: string]; cancel: [] }>()

const val = ref(props.initial)
const el = ref<HTMLInputElement>()

let done = false

const commit = () => {
  if (done) return

  done = true

  const v = val.value.trim()

  if (v && v !== props.initial) emit('commit', v)
  else emit('cancel')
}

onMounted(() => {
  el.value?.focus()

  const dot = props.initial.lastIndexOf('.')

  if (props.initial && dot > 0) el.value?.setSelectionRange(0, dot)
  else el.value?.select()
})
</script>

<template>
  <div class="ed-row cursor-default hover:bg-transparent">
    <span class="ed-lane" />
    <FileIcon
      :name="type === 'dir' ? '' : val || 'new.txt'"
      :dir="type === 'dir'"
      class="ed-lane"
    />
    <input
      ref="el"
      v-model="val"
      spellcheck="false"
      autocomplete="off"
      class="min-w-0 flex-1 rounded-4 border border-outline-gray-3 bg-surface-base px-1 py-0.5 text-sm text-ink-gray-8 outline-none focus:border-outline-gray-4"
      @keydown.enter.prevent="commit"
      @keydown.esc.prevent="$emit('cancel')"
      @blur="commit"
    />
  </div>
</template>
