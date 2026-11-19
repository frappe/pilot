<script setup lang="ts">
import { useTranslation } from '../translation'

const __ = useTranslation()

interface Props {
  name: string
  subtitle?: string
}

defineProps<Props>()
</script>

<template>
  <section class="divide-y divide-outline-gray-1 rounded-6 border border-outline-gray-1.5 px-4" :aria-label="__('Plan')">
    <div class="flex items-start justify-between gap-4 py-4">
      <div class="min-w-0">
        <p class="text-sm text-ink-gray-5">{{ __('Plan') }}</p>
        <p class="mt-1 text-xl-semibold text-ink-gray-9">{{ name }}</p>
        <p v-if="subtitle" class="mt-1 text-p-sm text-ink-gray-5">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.aside" class="shrink-0"><slot name="aside" /></div>
    </div>
    <slot />
  </section>
</template>
