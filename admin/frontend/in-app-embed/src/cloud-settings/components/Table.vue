<script setup lang="ts" generic="Row extends Record<string, unknown> & { id: string }">
import { useTranslation } from '../translation'

const __ = useTranslation()
interface Column {
  key: string
  label: string
  class?: string
  cellClass?: string
}

interface Props {
  columns: Column[]
  rows: Row[]
}

defineProps<Props>()
defineSlots<{ [name: string]: (props: { row: Row; column: Column; index: number }) => unknown }>()
</script>

<template>
  <div class="min-h-0 overflow-auto" tabindex="0" role="region" :aria-label="__('Settings table')">
    <table class="border-separate border-spacing-0 min-w-full text-left">
      <thead>
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            class="top-0 z-10 sticky bg-surface-gray-2 p-2 font-normal text-ink-gray-5 text-sm whitespace-nowrap"
            :class="column.class"
          >
            {{ column.label }}
          </th>
        </tr>
      </thead>

      <tbody>
        <tr v-for="(row, index) in rows" :key="row.id">
          <td
            v-for="column in columns"
            :key="column.key"
            class="px-2 h-10 whitespace-nowrap"
            :class="[column.class, column.cellClass]"
          >
            <slot :name="column.key" :row="row" :column="column" :index="index">
              {{ row[column.key] }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
tbody tr:not(:last-child) td {
  @apply border-b border-outline-gray-1;
}

th:first-child {
  @apply rounded-l-4;
}

th:last-child {
  @apply rounded-r-4;
}

tbody tr:hover td {
  @apply bg-surface-gray-2;
}

tbody tr:hover td:first-child {
  @apply rounded-l-4;
}

tbody tr:hover td:last-child {
  @apply rounded-r-4;
}
</style>
