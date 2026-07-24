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
      class="min-w-0 flex-1 rounded border border-outline-gray-3 bg-surface-white px-1 py-0.5 text-sm text-ink-gray-8 outline-none focus:border-outline-gray-4"
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
