<template>
  <div class="fixed inset-0 z-50 flex items-stretch justify-center sm:items-start sm:pt-[9vh]" @click="emit('close')">
    <div class="fixed inset-0 bg-black/25"></div>
    <div
      class="relative z-10 flex flex-col overflow-hidden bg-surface-white shadow-2xl max-sm:h-full max-sm:w-full sm:rounded-xl sm:border sm:border-outline-gray-2"
      :class="sizeClass"
      @click.stop
    >
      <div class="flex h-12 shrink-0 items-center gap-2 border-b border-outline-gray-1 px-3 sm:h-10">
        <span class="lucide-search h-4 w-4 shrink-0 text-ink-gray-4"></span>
        <slot name="input" />
        <slot name="actions" />
        <button class="ed-icon-button" aria-label="Close" @click="emit('close')">
          <span class="lucide-x h-[18px] w-[18px] text-current sm:h-4 sm:w-4"></span>
        </button>
      </div>
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({ sizeClass: { type: String, default: '' } })
const emit = defineEmits(['close'])
</script>
