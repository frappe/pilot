<script setup lang="ts">
import { useTranslation } from '../translation'
import { Button, SettingsBody, SettingsHeader } from 'frappe-ui'
import PanelSkeleton, { type SkeletonKind } from './PanelSkeleton.vue'

const __ = useTranslation()

interface Props {
  title: string
  description?: string
  loading?: boolean
  error?: string
  errorTitle?: string
  skeleton?: SkeletonKind
}

withDefaults(defineProps<Props>(), {
  description: '',
  error: '',
  errorTitle: '',
})
defineEmits(['retry'])
</script>

<template>
  <SettingsHeader
    class="!px-4 !pt-6 sm:!px-10 sm:!pt-9 relative z-10 pb-5 grid bg-surface-base grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4"
  >
    <h2 class="text-lg-semibold text-ink-gray-8">{{ title }}</h2>

    <p class="col-start-1 mt-1 text-base leading-5 text-ink-gray-6">{{ description }}</p>

    <slot name="actions" />
  </SettingsHeader>

  <SettingsBody viewport-class="px-4 pt-1 pb-10 sm:px-10 sm:pb-16">
    <PanelSkeleton v-if="loading" :kind="skeleton" />

    <div
      v-else-if="error"
      class="flex min-h-64 flex-col items-center justify-center rounded-6 border border-dashed border-outline-gray-3 px-6 py-12 text-center"
    >
      <div
        class="flex size-10 items-center justify-center rounded-6 bg-surface-gray-2 text-ink-gray-5"
      >
        <span class="lucide-triangle-alert size-4" aria-hidden="true" />
      </div>

      <p class="mt-4 text-base-medium text-ink-gray-8">
        {{ errorTitle || __("Couldn't load this section") }}
      </p>

      <p class="mt-1 max-w-sm text-p-sm text-ink-gray-5">{{ error }}</p>

      <Button class="mt-5" :label="__('Try again')" @click="$emit('retry')" />
    </div>

    <slot v-else />
  </SettingsBody>
</template>
