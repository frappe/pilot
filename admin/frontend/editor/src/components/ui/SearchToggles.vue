<script setup lang="ts">
import type { SearchOptions } from '@/api/search'

type Option = keyof SearchOptions

const props = defineProps<{ modelValue: SearchOptions }>()

const emit = defineEmits<{ 'update:modelValue': [value: SearchOptions]; change: [] }>()

const options: { id: Option; label: string; title: string }[] = [
  { id: 'case', label: 'Aa', title: 'Match case' },
  { id: 'word', label: 'ab', title: 'Whole word' },
  { id: 'regex', label: '.*', title: 'Use regular expression' },
]

const toggle = (id: Option) => {
  emit('update:modelValue', { ...props.modelValue, [id]: !props.modelValue[id] })
  emit('change')
}
</script>

<template>
  <div class="flex shrink-0 items-center gap-0.5">
    <button
      v-for="option in options"
      :key="option.id"
      class="flex h-6 w-6 items-center justify-center rounded-4 font-mono text-xs-semibold"
      :class="
        modelValue[option.id]
          ? 'bg-surface-gray-4 text-ink-gray-9'
          : 'text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8'
      "
      :title="option.title"
      :aria-pressed="!!modelValue[option.id]"
      @click="toggle(option.id)"
    >
      {{ option.label }}
    </button>
  </div>
</template>
