<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  label: string
  icon?: string
  selected?: boolean
  disabled?: boolean
  interactive?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  icon: 'lucide-globe',
  selected: false,
  disabled: false,
  interactive: true,
})

const stateClass = computed(() => {
  if (props.disabled) return 'opacity-60 cursor-not-allowed'
  if (props.selected) return 'bg-surface-gray-3'
  return props.interactive ? 'hover:bg-surface-alpha-gray-1' : ''
})
</script>

<template>
  <component
    :is="interactive ? 'button' : 'div'"
    :type="interactive ? 'button' : null"
    class="flex items-center gap-2.5 p-2.5 rounded-4 min-w-0 text-left"
    :class="[stateClass, interactive && 'transition duration-150 ease-[var(--ease-out)] active:scale-[0.98]']"
    :disabled="interactive && disabled ? true : null"
  >
    <span class="size-4 text-ink-gray-6 shrink-0" :class="icon" />
    <p class="flex-1 text-ink-gray-8 truncate">{{ label }}</p>
    <slot name="suffix" />
  </component>
</template>
