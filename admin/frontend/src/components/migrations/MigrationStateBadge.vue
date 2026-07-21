<template>
  <Badge :theme="stateTone(state)" variant="subtle" :label="stateLabel(state)" />
</template>

<script setup>
import { Badge } from 'frappe-ui'
import { stateLabel, stateTone } from '@/utils/migrationFormat'

defineProps({ state: { type: String, required: true } })
</script>
