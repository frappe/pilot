<script setup lang="ts" generic="Row extends Record<string, any>">
import { useVirtualList } from '@vueuse/core'
import { computed } from 'vue'

interface Column {
  key: string
  label: string
  class?: string
  cellClass?: string
}

interface Props {
  columns: Column[]
  rows: Row[]
  height?: string
  rowHeight?: number
  variant?: 'filled' | 'plain'
}

const props = withDefaults(defineProps<Props>(), {
  height: '',
  rowHeight: 40,
  variant: 'filled',
})

const headerClass = {
  filled: 'bg-surface-gray-2 dark:bg-surface-gray-1 text-sm first:rounded-l-4 last:rounded-r-4',
  plain: 'bg-surface-base border-b border-outline-gray-1.5 text-xs',
}

defineSlots<{
  [key: string]: (props: { row: Row; column: Column; index: number }) => any
}>()

const source = computed(() => props.rows)

const { list, containerProps } = useVirtualList(source, {
  itemHeight: () => props.rowHeight,
  overscan: 10,
})

const spacers = computed(() => {
  const first = list.value[0]?.index ?? 0

  return {
    top: first * props.rowHeight,
    bottom: (props.rows.length - first - list.value.length) * props.rowHeight,
  }
})
</script>

<template>
  <div class="min-h-0 overflow-auto" :class="height" v-bind="containerProps">
    <table class="border-separate border-spacing-0 min-w-full text-left">
      <thead>
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            class="top-0 z-10 sticky p-2 font-normal text-ink-gray-5 whitespace-nowrap"
            :class="[headerClass[variant], column.class]"
          >
            {{ column.label }}
          </th>
        </tr>
      </thead>

      <tbody>
        <tr v-if="spacers.top" class="spacer" aria-hidden="true">
          <td :colspan="columns.length" :style="{ height: `${spacers.top}px` }" />
        </tr>

        <tr v-for="{ data: row, index } in list" :key="row.id ?? index">
          <td
            v-for="column in columns"
            :key="column.key"
            class="px-2 whitespace-nowrap"
            :class="[column.class, column.cellClass]"
            :style="{ height: `${rowHeight}px` }"
          >
            <slot :name="column.key" :row="row" :column="column" :index="index">
              {{ row[column.key] }}
            </slot>
          </td>
        </tr>

        <tr v-if="spacers.bottom" class="spacer" aria-hidden="true">
          <td :colspan="columns.length" :style="{ height: `${spacers.bottom}px` }" />
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
tbody tr:not(:last-child) td {
  @apply border-b border-outline-gray-1;
}

tbody tr {
  position: relative;
}

tbody tr.spacer td {
  @apply border-0 p-0;
}

tbody tr:not(.spacer):hover td {
  @apply bg-surface-gray-1;
}

tbody tr:not(.spacer):hover td:first-child {
  @apply rounded-l-4;
}

tbody tr:not(.spacer):hover td:last-child {
  @apply rounded-r-4;
}
</style>
