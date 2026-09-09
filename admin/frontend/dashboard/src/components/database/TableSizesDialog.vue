<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Dialog, ErrorMessage, LoadingText } from 'frappe-ui'

import Table from '@/components/common/Table.vue'

import { apiErrorMessage } from '@/api/client'
import { databaseApi } from '@/api/database'
import { formatBytes } from '@/utils/format'

interface Props {
  site?: string
}

const props = withDefaults(defineProps<Props>(), {
  site: '',
})

const open = defineModel('open', { type: Boolean, default: false })

const columns = [
  { label: 'Table', key: 'name', class: 'w-[44%]' },
  { label: 'Data', key: 'data', class: 'w-[14%]' },
  { label: 'Index', key: 'index', class: 'w-[14%]' },
  { label: 'Claimable', key: 'claimable', class: 'w-[14%]' },
  { label: 'Total', key: 'total', class: 'w-[14%]' },
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

const load = async () => {
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

<template>
  <Dialog v-model="open" :title="`Table sizes on ${site}`" size="3xl">
    <LoadingText v-if="loading" class="justify-center py-10" />

    <ErrorMessage v-else-if="error" :message="error" />

    <p v-else-if="!tables.length" class="py-10 text-ink-gray-5 text-sm text-center">
      No results to display
    </p>

    <Table v-else :columns="columns" :rows="rows" height="max-h-[60vh]" />
  </Dialog>
</template>
