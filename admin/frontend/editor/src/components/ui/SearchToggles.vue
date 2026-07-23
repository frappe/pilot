<template>
  <div class="flex shrink-0 items-center gap-0.5">
    <button
      v-for="option in OPTIONS"
      :key="option.id"
      class="flex h-6 w-6 items-center justify-center rounded font-mono text-xs font-semibold"
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

<script setup>
const props = defineProps({ modelValue: { type: Object, required: true } })
const emit = defineEmits(['update:modelValue', 'change'])

const OPTIONS = [
  { id: 'case', label: 'Aa', title: 'Match case' },
  { id: 'word', label: 'ab', title: 'Whole word' },
  { id: 'regex', label: '.*', title: 'Use regular expression' },
]

function toggle(id) {
  emit('update:modelValue', { ...props.modelValue, [id]: !props.modelValue[id] })
  emit('change')
}
</script>
