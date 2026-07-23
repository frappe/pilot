<template>
  <div class="flex items-center gap-1.5 py-2 px-1.5 sm:py-1">
    <span class="w-5 shrink-0 sm:w-3.5" />
    <FileIcon
      :name="type === 'dir' ? '' : val || 'new.txt'"
      :dir="type === 'dir'"
      class="h-5 w-5 shrink-0 sm:h-4 sm:w-4"
    />
    <input
      ref="el"
      v-model="val"
      spellcheck="false"
      autocomplete="off"
      class="min-w-0 flex-1 rounded border border-outline-gray-3 bg-surface-white px-1 py-1 text-base text-ink-gray-8 outline-none focus:border-outline-gray-4 sm:py-px sm:text-sm"
      @keydown.enter.prevent="commit"
      @keydown.esc.prevent="$emit('cancel')"
      @blur="commit"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import FileIcon from '@/components/FileIcon.vue'

const props = defineProps({
  type: { type: String, default: 'file' },
  initial: { type: String, default: '' },
})
const emit = defineEmits(['commit', 'cancel'])

const val = ref(props.initial)
const el = ref(null)
let done = false

function commit() {
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
