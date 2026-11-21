<script setup lang="ts">
import { Skeleton } from 'frappe-ui'

withDefaults(defineProps<{ rows?: number; chart?: string }>(), { rows: 0, chart: '' })

const widths = ['w-2/5', 'w-3/5', 'w-1/2', 'w-2/3', 'w-1/3']
</script>

<template>
  <div v-if="chart" class="flex flex-col gap-3">
    <Skeleton class="rounded-4 w-20 h-4" />
    <Skeleton class="opacity-60 rounded-4 w-full" :class="chart" />
  </div>

  <div v-else class="flex flex-col">
    <span v-for="index in rows" :key="index" class="flex justify-between items-center gap-6 h-9">
      <Skeleton class="rounded-4 h-3" :class="widths[(index - 1) % widths.length]" />
      <Skeleton class="rounded-4 w-10 h-3" />
    </span>
  </div>
</template>
