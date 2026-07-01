<template>
  <div class="flex items-center gap-2.5">
    <div
      class="place-items-center grid rounded-lg size-8 overflow-hidden shrink-0"
      :style="logoStyle"
    >
      <img
        v-if="app.logo_url && !imageFailed"
        :src="app.logo_url"
        :alt="app.title"
        class="size-full object-contain"
        @error="imageFailed = true"
      />
      <span v-else class="font-bold text-white text-sm leading-none">
        {{ app.title?.[0]?.toUpperCase() || app.name?.[0]?.toUpperCase() }}
      </span>
    </div>

    <div
      class="flex flex-1 justify-between items-center gap-2 min-w-0"
      :class="showDivider ? 'py-4 border-b border-outline-gray-2' : 'py-2'"
    >
      <div class="min-w-0">
        <div class="flex items-center gap-1.5">
          <span class="font-medium text-ink-gray-8 text-base truncate">{{ app.title }}</span>
          <span v-if="app.label" class="text-ink-gray-5 text-p-xs shrink-0">{{ app.label }}</span>
        </div>
        <div class="text-ink-gray-5 text-p-sm truncate">
          {{ app.description }}
        </div>
      </div>

      <slot name="actions">
        <span v-if="app.installed" class="flex items-center gap-1 shrink-0 text-ink-gray-5 text-p-sm">
          <span class="size-4 text-ink-green-6 lucide-check"></span> Installed
        </span>
        <span v-else-if="!app.compatible" class="text-ink-gray-4 text-xs shrink-0">
          Needs Version {{ app.needs }}
        </span>
        <Button v-else variant="subtle" @click="$emit('install', app)">Install</Button>
      </slot>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button } from 'frappe-ui'
import { logoColor } from '@/composables/useMarketplace'

const props = defineProps({
  app: { type: Object, required: true },
  showDivider: { type: Boolean, default: true },
})
defineEmits(['install'])

const imageFailed = ref(false)

const logoStyle = computed(() => {
  if (props.app.logo_url && !imageFailed.value) return {}
  return { background: logoColor(props.app.name) }
})
</script>
