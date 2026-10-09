<script setup lang="ts">
import { Alert } from 'frappe-ui'
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
  <Alert
    v-if="message"
    theme="red"
    :title="message"
    :primary-action="actionLabel && actionUrl ? { label: actionLabel, onClick: () => openExternal(actionUrl) } : undefined"
  />
</template>
