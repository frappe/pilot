<script setup lang="ts">
import { Skeleton } from 'frappe-ui'

interface Props {
  rows?: number
}

withDefaults(defineProps<Props>(), {
  rows: 10,
})
</script>

<template>
  <div
    v-for="i in rows"
    :key="i"
    class="items-center gap-3 grid grid-cols-[3fr_2fr_1fr_1fr] px-2 h-10"
    :class="i < rows ? 'border-b border-outline-gray-1' : ''"
  >
    <Skeleton v-for="c in 4" :key="c" class="rounded-4 w-full h-4" />
  </div>
</template>
