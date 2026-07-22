<template>
  <div class="flex items-center gap-3">
    <span class="place-items-center grid rounded-lg size-9 shrink-0"
      :class="highlight ? 'bg-surface-amber-2' : 'bg-surface-gray-2'">
      <span class="size-4" :class="[icon, highlight ? 'text-ink-amber-3' : 'text-ink-gray-6']" />
    </span>
    <div class="min-w-0">
      <p class="font-semibold text-ink-gray-9 text-base truncate">{{ value }}</p>
      <p class="text-ink-gray-5 text-xs truncate">{{ label }}</p>
    </div>
  </div>
</template>

<script setup>
defineProps({
  value: { type: [String, Number], required: true },
  label: { type: String, required: true },
  icon: { type: String, default: 'lucide-activity' },
  highlight: { type: Boolean, default: false },
})
</script>
