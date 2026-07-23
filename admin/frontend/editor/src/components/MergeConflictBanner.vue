<template>
  <div
    v-if="conflicts.length"
    class="flex items-center justify-between border-b border-outline-gray-2 bg-surface-amber-1 px-3 py-2"
  >
    <div class="flex items-center gap-2 text-sm text-ink-amber-3">
      <span class="lucide-alert-triangle h-4 w-4"></span>
      <span class="font-medium">{{ conflicts.length }} merge conflict{{ conflicts.length > 1 ? 's' : '' }}</span>
    </div>
    <div class="flex items-center gap-3">
      <button
        class="text-xs text-ink-blue-3 transition hover:text-ink-blue-4 hover:underline"
        @click="emit('accept-all-current')"
      >
        accept current
      </button>
      <button
        class="text-xs text-ink-blue-3 transition hover:text-ink-blue-4 hover:underline"
        @click="emit('accept-all-incoming')"
      >
        accept incoming
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  conflicts: { type: Array, default: () => [] },
})
const emit = defineEmits(['accept-all-current', 'accept-all-incoming'])
</script>
