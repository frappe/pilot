<script setup>
import { ref, computed, onMounted } from 'vue'
import { ListView, LoadingText, ErrorMessage } from 'frappe-ui'

const logs = ref([])
const loading = ref(true)
const error = ref('')

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fmtDate(iso) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function fmtLines(lines) {
  return Number(lines ?? 0).toLocaleString()
}

const columns = [
  { label: 'File', key: 'filename', width: '200px' },
  { label: 'Process', key: 'process_name', width: '150px' },
  { label: 'Size', key: '_size', width: '80px' },
  { label: 'Lines', key: '_lines', width: '100px' },
  { label: 'Last Modified', key: '_modified' },
]

const rows = computed(() =>
  logs.value.map(l => ({
    ...l,
    _size: fmtSize(l.size_bytes),
    _lines: fmtLines(l.line_count),
    _modified: fmtDate(l.last_modified),
  }))
)

onMounted(async () => {
  try {
    const res = await fetch('/api/logs/')
    logs.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <LoadingText v-if="loading" />
    <ErrorMessage v-else-if="error" :message="error" />

    <div v-else>
      <ListView
        :columns="columns"
        :rows="rows"
        row-key="filename"
        :options="{
          getRowRoute: (row) => `/logs/${row.filename}`,
          selectable: false,
          showTooltip: false,
        }"
      />
    </div>
  </div>
</template>
