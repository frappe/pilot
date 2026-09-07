<script setup lang="ts" generic="Row extends Record<string, any>">
import Scrollbar from '@/components/common/Scrollbar.vue'

interface Column {
  key: string
  label: string
  class?: string
}

interface Props {
  columns: Column[]
  rows: Row[]
  height?: string
}

defineProps<Props>()

defineSlots<{
  [key: string]: (props: { row: Row; column: Column; index: number }) => any
}>()
</script>

<template>
  <Scrollbar class="min-h-0" :class="height || 'flex-1'">
    <table class="border-separate border-spacing-0 min-w-full text-left">
      <thead class="top-0 z-10 sticky">
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            class="bg-surface-gray-2 p-2 font-normal text-ink-gray-5 text-sm whitespace-nowrap"
            :class="column.class"
          >
            {{ column.label }}
          </th>
        </tr>
      </thead>

      <tbody>
        <tr v-for="(row, index) in rows" :key="row.id ?? index">
          <td
            v-for="column in columns"
            :key="column.key"
            class="px-2 h-10 whitespace-nowrap"
            :class="column.class"
          >
            <slot :name="column.key" :row="row" :column="column" :index="index">
              {{ row[column.key] }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </Scrollbar>
</template>

<style scoped>
tbody tr:not(:last-child) td {
  @apply border-b border-outline-gray-1;
}

tbody tr:hover td {
  @apply bg-surface-gray-1;
}

th:first-child,
tbody tr:hover td:first-child {
  @apply rounded-l-4;
}

th:last-child,
tbody tr:hover td:last-child {
  @apply rounded-r-4;
}
</style>
