<script setup lang="ts">
import { ListCell, ListRow } from 'frappe-ui/list'

interface Props {
  label: string
  value?: string
  detail?: string
  /** Greys the value, for a missing one such as "No card added". */
  isEmpty?: boolean
}

defineProps<Props>()
</script>

<template>
  <ListRow class="py-3">
    <ListCell class="flex-col !items-start">
      <span class="text-sm text-ink-gray-5">{{ label }}</span>
      <slot name="value">
        <span class="text-base" :class="isEmpty ? 'text-ink-gray-5' : 'text-ink-gray-8'">{{ value }}</span>
      </slot>
      <slot name="detail">
        <span v-if="detail" class="text-p-sm text-ink-gray-5">{{ detail }}</span>
      </slot>
    </ListCell>
    <ListCell v-if="$slots.default"><slot /></ListCell>
  </ListRow>
</template>
