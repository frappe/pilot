<template>
  <div class="bg-surface-white border rounded-lg border-outline-gray-2">
    <Button variant="ghost" class="!justify-start !px-4 !py-3 !h-auto w-full" @click="open = !open">
      <template #prefix>
        <span class="size-4 text-ink-gray-5" :class="icon" />
      </template>
      <span class="font-medium text-ink-gray-8 text-base">{{ title }}</span>
      <span v-if="subtitle" class="ml-2 font-normal text-ink-gray-5 text-sm">{{ subtitle }}</span>
      <template #suffix>
        <span class="ml-auto size-4 text-ink-gray-4"
          :class="open ? 'lucide-chevron-up' : 'lucide-chevron-down'" />
      </template>
    </Button>

    <div v-if="open" class="px-4 pb-3 border-t border-outline-gray-2">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Button } from 'frappe-ui'

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  icon: { type: String, default: 'lucide-activity' },
})

const open = ref(false)
</script>
