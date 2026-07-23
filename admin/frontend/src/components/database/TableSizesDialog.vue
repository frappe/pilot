<template>
  <Dialog v-model="open" :options="{ title: `Table sizes on ${site}`, size: '3xl' }">
    <template #body-content>
      <div v-if="loading" class="flex justify-center py-10">
        <LoadingText />
      </div>

      <ErrorMessage v-else-if="error" :message="error" />

      <p v-else-if="!tables.length" class="py-10 text-ink-gray-5 text-sm text-center">
        No results to display
      </p>

      <div v-else class="max-h-[60vh] overflow-y-auto">
        <ListView class="!w-full" :columns="columns" :rows="rows" row-key="name"
          :options="{ selectable: false, showTooltip: false }" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Dialog, ErrorMessage, ListView, LoadingText } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { databaseApi } from '@/api/database'
import { formatBytes } from '@/utils/format'

const props = defineProps({
  site: { type: String, default: '' },
})

const open = defineModel('open', { type: Boolean, default: false })

const columns = [
  { label: 'Table', key: 'name', align: 'left', width: 3 },
  { label: 'Data', key: 'data', align: 'right', width: 1 },
  { label: 'Index', key: 'index', align: 'right', width: 1 },
  { label: 'Claimable', key: 'claimable', align: 'right', width: 1 },
  { label: 'Total', key: 'total', align: 'right', width: 1 },
]

const tables = ref([])
const loading = ref(false)
const error = ref('')

const rows = computed(() =>
  tables.value.map((table) => ({
    name: table.name,
    data: formatBytes(table.data_bytes),
    index: formatBytes(table.index_bytes),
    claimable: table.claimable_bytes == null ? '—' : formatBytes(table.claimable_bytes),
    total: formatBytes(table.data_bytes + table.index_bytes),
  })),
)

watch(open, (isOpen) => {
  if (isOpen) load()
})

async function load() {
  loading.value = true
  error.value = ''
  tables.value = []
  try {
    const result = await databaseApi.tableSizes(props.site)
    if (result?.error) throw new Error(apiErrorMessage(result, 'Could not read table sizes.'))
    tables.value = Array.isArray(result) ? result : []
  } catch (e) {
    error.value = e.message || 'Could not read table sizes.'
  } finally {
    loading.value = false
  }
}
</script>
