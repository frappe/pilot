<script setup lang="ts">
interface Props {
  icon?: string
  title: string
  description?: string
  // Off when the state already sits inside a bordered panel.
  bordered?: boolean
  // For empty states that sit among other content rather than filling a page.
  compact?: boolean
}

withDefaults(defineProps<Props>(), { bordered: true })
</script>

<template>
  <div
    class="flex flex-col items-center px-6 text-center"
    :class="[
      bordered ? 'border border-outline-gray-2 border-dashed rounded-7' : '',
      compact ? 'gap-3 py-6' : 'gap-4 py-12',
    ]"
  >
    <span
      v-if="icon"
      class="place-items-center grid bg-surface-gray-2 text-ink-gray-5 shrink-0"
      :class="compact ? 'rounded-5 size-8' : 'rounded-6 size-10'"
    >
      <span :class="[compact ? 'size-4' : 'size-5', icon]" />
    </span>

    <div>
      <p class="font-medium text-ink-gray-7">{{ title }}</p>
      <p v-if="description" class="mt-1 max-w-xs text-ink-gray-5 text-p-sm">{{ description }}</p>
    </div>

    <slot />
  </div>
</template>
