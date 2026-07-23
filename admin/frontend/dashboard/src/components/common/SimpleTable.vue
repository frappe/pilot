<template>
  <div :class="bordered ? 'border rounded-lg border-outline-gray-2 overflow-hidden' : ''">
    <div :style="minHeight && !rows.length ? { minHeight } : {}">
      <table class="w-full min-w-max text-sm">
        <thead>
          <tr class="bg-surface-gray-2 text-ink-gray-5 text-xs text-left uppercase">
            <th v-if="showIndex" class="px-3 py-2 w-8">#</th>
            <th v-for="column in columns" :key="column.key" class="px-3 py-2 whitespace-nowrap">
              {{ column.label }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in rows" :key="i">
            <td v-if="showIndex" class="px-3 py-2 border-t border-outline-gray-2 tabular-nums text-ink-gray-4 text-xs">
              {{ indexOffset + i + 1 }}
            </td>
            <td v-for="(column, j) in columns" :key="column.key"
              class="px-3 py-2 border-t border-outline-gray-2 text-ink-gray-8"
              :class="[mono && 'font-mono', truncate ? 'max-w-xs' : j > 0 && !showIndex && 'break-all']">
              <span v-if="showNull && row[column.key] === null" class="text-ink-gray-3 italic">null</span>
              <span v-else :class="truncate && 'block truncate'">{{ row[column.key] }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="emptyText && !rows.length" class="flex justify-center items-center py-16 text-ink-gray-4 text-sm">
        {{ emptyText }}
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  // [{ key, label }]
  columns: { type: Array, required: true },
  // plain objects keyed by each column's `key`
  rows: { type: Array, required: true },
  bordered: { type: Boolean, default: true },
  showIndex: { type: Boolean, default: false },
  indexOffset: { type: Number, default: 0 },
  minHeight: { type: String, default: '' },
  mono: { type: Boolean, default: true },
  truncate: { type: Boolean, default: false },
  showNull: { type: Boolean, default: false },
  emptyText: { type: String, default: '' },
})
</script>
