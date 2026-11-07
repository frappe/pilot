<script setup lang="ts">
import { Skeleton } from 'frappe-ui'
import { useTranslation } from '../translation'

const __ = useTranslation()

export type SkeletonKind = 'list' | 'table' | 'apps' | 'tiles'

interface Props {
  /** The shape of the content that is loading, so the page does not jump when it arrives. */
  kind?: SkeletonKind
  rows?: number
}

withDefaults(defineProps<Props>(), { kind: 'list', rows: 3 })
</script>

<template>
  <div role="status" :aria-label="__('Loading')">
    <div aria-hidden="true">
      <div v-if="kind === 'list'" class="divide-y divide-outline-gray-1.5 rounded-6 border border-outline-gray-1.5 px-4">
        <div v-for="row in rows" :key="row" class="flex items-center gap-4 py-3.5">
          <div class="min-w-0 flex-1 space-y-2">
            <Skeleton class="h-3 w-20 rounded-4" />
            <Skeleton class="h-4 rounded-4" :class="row % 2 ? 'w-48' : 'w-36'" />
          </div>
          <Skeleton class="h-7 w-24 shrink-0 rounded-4" />
        </div>
      </div>

      <div v-else-if="kind === 'table'">
        <div class="flex gap-6 border-b border-outline-gray-1 px-2 py-2.5">
          <Skeleton class="h-3 w-16 rounded-4" />
          <Skeleton class="ml-auto h-3 w-14 rounded-4" />
          <Skeleton class="h-3 w-14 rounded-4" />
        </div>
        <div v-for="row in rows" :key="row" class="flex items-center gap-6 border-b border-outline-gray-1 px-2 py-3.5 last:border-b-0">
          <Skeleton class="h-4 rounded-4" :class="row % 2 ? 'w-32' : 'w-28'" />
          <Skeleton class="ml-auto h-4 w-16 rounded-4" />
          <Skeleton class="h-4 w-14 rounded-4" />
        </div>
      </div>

      <div v-else-if="kind === 'apps'">
        <div class="flex flex-col gap-2 sm:flex-row">
          <Skeleton class="h-7 flex-1 rounded-4" />
          <Skeleton class="h-7 rounded-4 sm:w-44" />
          <Skeleton class="h-7 rounded-4 sm:w-36" />
        </div>
        <Skeleton class="mb-3 mt-6 h-4 w-24 rounded-4" />
        <div class="grid gap-1 sm:grid-cols-2">
          <div v-for="row in rows * 2" :key="row" class="flex items-center gap-3 py-2.5">
            <Skeleton class="size-6 shrink-0 rounded-4" />
            <Skeleton class="h-4 rounded-4" :class="row % 3 ? 'w-28' : 'w-36'" />
          </div>
        </div>
      </div>

      <div v-else class="grid gap-3 sm:grid-cols-2">
        <div v-for="row in rows + 1" :key="row" class="flex items-center gap-3 rounded-6 border border-outline-gray-2 p-4">
          <Skeleton class="size-10 shrink-0 rounded-6" />
          <div class="min-w-0 flex-1 space-y-2">
            <Skeleton class="h-3 w-20 rounded-4" />
            <Skeleton class="h-4 w-28 rounded-4" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
