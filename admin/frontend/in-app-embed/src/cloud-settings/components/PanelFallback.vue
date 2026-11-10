<script setup lang="ts">
import { ErrorMessage, Skeleton } from 'frappe-ui'
import PanelSkeleton from './PanelSkeleton.vue'
import { useTranslation } from '../translation'

defineProps<{ error?: Error }>()

const __ = useTranslation()
</script>

<template>
  <div class="px-4 pt-6 sm:px-10 sm:pt-9">
    <ErrorMessage
      v-if="error"
      :message="__('Could not load this panel. Check your connection, then open Cloud Settings again.')"
    />

    <div v-else class="space-y-6">
      <div class="space-y-2" aria-hidden="true">
        <Skeleton class="h-5 w-32 rounded-4" />
        <Skeleton class="h-4 w-64 max-w-full rounded-4" />
      </div>
      <PanelSkeleton />
    </div>
  </div>
</template>
