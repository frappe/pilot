<script setup lang="ts">
interface Props {
  label: string
  description?: string
  as?: 'div' | 'button'
  interactive?: boolean
}

withDefaults(defineProps<Props>(), {
  description: '',
  as: 'div',
  interactive: false,
})
</script>

<template>
  <component
    :is="as"
    :type="as === 'button' ? 'button' : undefined"
    class="flex justify-between items-center gap-x-2.5 px-2.5 py-3"
    :class="interactive ? 'w-full rounded-4 transition-colors cursor-pointer text-left hover:bg-surface-alpha-gray-1' : ''"
  >
    <div class="flex flex-col gap-1">
      <p class="font-medium text-ink-gray-8">{{ label }}</p>
      <p v-if="description" class="text-ink-gray-6 text-p-sm">{{ description }}</p>
    </div>

    <div class="ml-4 shrink-0">
      <slot />
    </div>
  </component>
</template>
