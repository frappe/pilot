<script setup lang="ts">
import { Badge } from 'frappe-ui'

import { stateLabel, stateTone } from '@/utils/updateFormat'

interface Props {
  state: string
}

defineProps<Props>()
</script>

<template>
  <Badge :theme="stateTone(state)" :label="stateLabel(state)" />
</template>
