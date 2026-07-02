<template>
  <div class="border rounded-lg border-outline-gray-2 overflow-hidden">
    <table class="w-full text-sm">
      <thead>
        <tr class="bg-surface-gray-2 text-ink-gray-5 text-xs text-left uppercase">
          <th v-for="column in columns" :key="column.key" class="px-3 py-2">{{ column.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in rows" :key="i">
          <td v-for="(column, j) in columns" :key="column.key"
            class="px-3 py-2 border-t border-outline-gray-2 font-mono text-ink-gray-8" :class="j > 0 && 'break-all'">
            {{ row[column.key] }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
defineProps({
  // [{ key, label }]
  columns: { type: Array, required: true },
  // plain objects keyed by each column's `key`
  rows: { type: Array, required: true },
})
</script>
