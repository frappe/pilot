<script setup lang="ts">
interface Props {
  label: string
  hint?: string
  /** A Lucide icon class. Without an icon or an image, the tile shows the label's first letter. */
  icon?: string
  image?: string
  selected?: boolean
  disabled?: boolean
}

defineProps<Props>()
</script>

<template>
  <button
    type="button"
    class="grid w-full grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 rounded-6 border p-3.5 text-left transition-colors hover:bg-surface-gray-1 disabled:opacity-60"
    :class="selected ? 'border-outline-gray-5' : 'border-outline-gray-2'"
    :aria-pressed="selected"
    :disabled="disabled"
  >
    <span class="row-span-2 mt-0.5 flex size-6 items-center justify-center" aria-hidden="true">
      <span v-if="icon" :class="[icon, 'size-5 text-ink-gray-7']" />
      <img v-else-if="image" :src="image" alt="" class="max-h-full max-w-full rounded-4 object-contain" />
      <span v-else class="flex size-6 items-center justify-center rounded-4 bg-surface-gray-3 text-p-sm-bold text-ink-gray-8">
        {{ label.charAt(0) }}
      </span>
    </span>

    <span class="text-base-medium text-ink-gray-8">{{ label }}</span>
    <span v-if="hint" class="col-start-2 text-p-sm text-ink-gray-5">{{ hint }}</span>
  </button>
</template>
