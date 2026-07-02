<template>
  <div class="flex items-center gap-2.5">
    <div class="place-items-center grid rounded-lg size-8 overflow-hidden shrink-0" :style="logoStyle">
      <img v-if="app.logo_url && !imageFailed" :src="app.logo_url" :alt="app.title" class="size-full object-contain"
        @error="imageFailed = true" />
      <span v-else class="font-bold text-white text-sm leading-none">
        {{ app.title?.[0]?.toUpperCase() || app.name?.[0]?.toUpperCase() }}
      </span>
    </div>

    <div class="flex flex-1 justify-between items-center gap-2 min-w-0"
      :class="showDivider ? 'py-4 border-b border-outline-gray-2' : 'py-2'">
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
        <span v-if="app.installed" class="flex items-center gap-1 text-ink-gray-5 text-p-sm shrink-0">
          <span class="size-4 text-ink-green-6 lucide-check"></span> Installed
        </span>
        <Tooltip v-else-if="!app.compatible"
          :text="`Requires ${app.needs ? `Frappe ${props.app.needs}` : 'a newer Frappe'} version`">
          <Button variant="subtle" class="!bg-surface-gray-2 !text-ink-gray-4" @click="showIncompatible = true">
            Install
          </Button>
        </Tooltip>
        <Button v-else variant="subtle" @click="$emit('install', app)">Install</Button>
      </slot>
    </div>

    <Dialog v-model="showIncompatible" :options="{ title: 'Incompatible App', size: 'sm' }">
      <template #body-content>
        <p class="text-ink-gray-7 text-sm">{{ incompatibleReason }}</p>
        <div class="flex flex-col gap-1.5 mt-3 text-sm">
          <div class="flex justify-between">
            <span class="text-ink-gray-5">Current version</span>
            <span class="font-medium text-ink-gray-8">{{ app.frappe_version || 'Unknown' }}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-ink-gray-5">Required version</span>
            <span class="font-medium text-ink-gray-8">{{ app.needs || 'Not specified' }}</span>
          </div>
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Dialog, Tooltip } from 'frappe-ui'
import { logoColor } from '@/composables/useMarketplace'

const props = defineProps({
  app: { type: Object, required: true },
  showDivider: { type: Boolean, default: true },
})
defineEmits(['install'])

const imageFailed = ref(false)
const showIncompatible = ref(false)

const incompatibleReason = computed(
  () => `${props.app.title} requires ${props.app.needs ? `Frappe ${props.app.needs}` : 'a newer Frappe version'} to install.`,
)

const logoStyle = computed(() => {
  if (props.app.logo_url && !imageFailed.value) return {}
  return { background: logoColor(props.app.name) }
})
</script>
