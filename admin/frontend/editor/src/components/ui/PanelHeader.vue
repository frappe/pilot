<template>
  <div
    class="group flex h-11 shrink-0 items-center gap-1 border-b border-outline-gray-1 px-3 sm:h-9 sm:border-b-0"
  >
    <span
      class="shrink-0 truncate text-sm font-semibold text-ink-gray-8 sm:text-2xs sm:font-medium sm:uppercase sm:tracking-wide sm:text-ink-gray-5"
    >
      {{ title }}
    </span>
    <!-- Context is what the panel is looking at, not something you do to it, so
         it stays visible while actions reveal on hover. -->
    <div class="flex min-w-0 flex-1 items-center justify-end">
      <slot name="context" />
    </div>
    <!-- Actions stay mounted so the row height never shifts; desktop reveals them on hover. -->
    <div class="flex items-center gap-0.5 transition sm:opacity-0 sm:group-hover:opacity-100">
      <slot name="actions" />
    </div>
    <button class="ed-icon-button sm:hidden" aria-label="Close" @click="emit('close')">
      <span class="lucide-x h-[18px] w-[18px] text-current"></span>
    </button>
  </div>
</template>

<script setup>
defineProps({ title: { type: String, required: true } })
const emit = defineEmits(['close'])
</script>
