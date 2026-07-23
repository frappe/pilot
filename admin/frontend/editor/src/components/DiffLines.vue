<template>
  <div v-if="!view.length" class="p-4 text-xs text-ink-gray-4">{{ emptyText }}</div>
  <template v-else>
    <div v-for="(h, hi) in view" :key="hi">
      <div class="bg-surface-gray-1 px-3 py-1.5 font-mono text-2xs text-ink-gray-5 sm:py-1">{{ h.label }}</div>
      <div class="font-mono text-xs leading-[1.5]">
        <div v-for="row in h.rows" :key="row.key" class="flex py-1 sm:py-0" :class="rowBg(row.type)">
          <span class="w-9 shrink-0 select-none pr-2 text-right text-2xs tabular-nums text-ink-gray-4 sm:w-10">{{ row.oldNo }}</span>
          <span class="w-9 shrink-0 select-none pr-2 text-right text-2xs tabular-nums text-ink-gray-4 sm:w-10">{{ row.newNo }}</span>
          <span class="w-3 shrink-0 select-none text-center text-xs" :class="signColor(row.type)">{{ row.sign }}</span>
          <span class="whitespace-pre text-xs text-ink-gray-8">{{ row.content }}</span>
        </div>
      </div>
    </div>
  </template>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  parsed: { type: Object, default: () => ({ hunks: [] }) },
  emptyText: { type: String, default: 'No changes.' },
})

const view = computed(() =>
  (props.parsed.hunks || []).map((h, hi) => {
    let oln = h.oldStart
    let nln = h.newStart
    const rows = []
    h.lines.forEach((ln, li) => {
      const key = hi + ':' + li
      if (ln.type === '\\') {
        rows.push({ key, sign: '', type: '\\', oldNo: '', newNo: '', content: ln.text })
        return
      }
      const text = ln.text.slice(1)
      if (ln.type === ' ') rows.push({ key, sign: ' ', type: ' ', oldNo: oln++, newNo: nln++, content: text })
      else if (ln.type === '-') rows.push({ key, sign: '-', type: '-', oldNo: oln++, newNo: '', content: text })
      else rows.push({ key, sign: '+', type: '+', oldNo: '', newNo: nln++, content: text })
    })
    return { label: '@@ -' + h.oldStart + ' +' + h.newStart + ' @@' + (h.context || ''), rows }
  }),
)

function rowBg(type) {
  if (type === '+') return 'bg-surface-green-1'
  if (type === '-') return 'bg-surface-red-1'
  return ''
}
function signColor(type) {
  if (type === '+') return 'text-ink-green-3'
  if (type === '-') return 'text-ink-red-4'
  return 'text-ink-gray-4'
}
</script>
