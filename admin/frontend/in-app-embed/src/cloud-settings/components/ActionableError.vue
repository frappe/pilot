<script setup lang="ts">
import { Button } from 'frappe-ui'
import { openExternal } from '../external'

interface Props {
  message?: string
  actionLabel?: string
  actionUrl?: string
}

withDefaults(defineProps<Props>(), {
  message: '',
  actionLabel: '',
  actionUrl: '',
})
</script>

<template>
  <div
    v-if="message"
    class="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2 rounded-6 border border-outline-red-2 bg-surface-red-1 p-3"
    role="alert"
  >
    <span class="lucide-triangle-alert mt-0.5 size-4 shrink-0 text-ink-red-8" aria-hidden="true" />
    <p class="text-p-sm text-ink-red-8">{{ message }}</p>

    <Button
      v-if="actionLabel && actionUrl"
      class="col-start-2 mt-2 justify-self-start"
      icon-right="lucide-arrow-up-right"
      @click="openExternal(actionUrl)"
    >
      {{ actionLabel }}
    </Button>
  </div>
</template>
