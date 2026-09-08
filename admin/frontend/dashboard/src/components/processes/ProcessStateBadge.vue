<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from 'frappe-ui'

import { processState } from '@/utils/processFormat'

interface Props {
  state: string
}

const props = defineProps<Props>()

const config = computed(() => processState(props.state))
</script>

<template>
  <Badge :theme="config.theme" :label="config.label">
    <template #prefix>
      <span class="relative flex size-1.5">
        <span
          v-if="config.live"
          class="absolute inline-flex bg-current opacity-75 rounded-full size-full animate-ping"
        />
        <span class="inline-flex bg-current rounded-full size-full" />
      </span>
    </template>
  </Badge>
</template>
