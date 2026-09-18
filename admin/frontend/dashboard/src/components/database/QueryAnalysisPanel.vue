<script setup lang="ts">
import { Button, Dialog, ErrorMessage, TabButtons } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

import Table from '@/components/common/Table.vue'
import DatabasePanel from '@/components/database/DatabasePanel.vue'
import PerformanceSchemaNotice from '@/components/database/PerformanceSchemaNotice.vue'

import { apiErrorMessage } from '@/api/client'
import { databaseApi } from '@/api/database'
import { formatCount, formatMilliseconds } from '@/utils/format'

const props = defineProps({
  site: { type: String, default: '' },
  badge: { type: String, default: '' },
  enabled: { type: Boolean, default: false },
  showSite: { type: Boolean, default: false },
  siteByDatabase: { type: Object, default: () => ({}) },
})

const pageSize = 20

const tab = ref('time_consuming_queries')

const rows = ref([])
const hasNextPage = ref(false)
const loading = ref(false)
const error = ref('')
const loaded = ref(false)

const siteLabel = (database) => props.siteByDatabase[database] || database || '—'

const withSite = (columns) =>
  props.showSite ? [{ key: 'database', label: 'Site' }, ...columns] : columns

const tabOptions = [
  { label: 'Time consuming', value: 'time_consuming_queries' },
  { label: 'Full table scans', value: 'full_table_scan_queries' },
]

const columnsByTab = {
  time_consuming_queries: [
    { key: 'percent', label: 'Percentage' },
    { key: 'calls', label: 'Calls' },
    { key: 'average_time_ms', label: 'Avg Time' },
    { key: 'query', label: 'Query' },
  ],
  full_table_scan_queries: [
    { key: 'rows_examined', label: 'Rows Examined' },
    { key: 'rows_sent', label: 'Rows Sent' },
    { key: 'calls', label: 'Calls' },
    { key: 'query', label: 'Query' },
  ],
}

const columns = computed(() => withSite(columnsByTab[tab.value]))

const load = async (offset = 0) => {
  loading.value = true
  error.value = ''
  try {
    const result = await databaseApi.performanceReport(tab.value, props.site, pageSize, offset)
    if (result?.error) throw new Error(apiErrorMessage(result, 'Could not load the query report.'))
    rows.value = offset ? [...rows.value, ...result.data] : result.data
    hasNextPage.value = result.has_next_page
    loaded.value = true
  } catch (caught) {
    error.value = caught.message || 'Could not load the query report.'
  } finally {
    loading.value = false
  }
}

watch(tab, () => load())

watch(
  () => props.site,
  () => loaded.value && load(),
)

const openQuery = ref('')
</script>

<template>
  <DatabasePanel
    title="SQL Query Analysis"
    subtitle="Check the concerning queries that might be affecting your database performance"
    :badge="badge"
    :loading="loading"
    @open="load()"
    @refresh="load()"
  >
    <ErrorMessage v-if="error" :message="error" class="m-4" />

    <template v-else>
      <div class="px-4 pb-3">
        <TabButtons v-model="tab" :options="tabOptions" size="sm" />
      </div>

      <PerformanceSchemaNotice v-if="!enabled" />

      <p v-else-if="!rows.length" class="py-10 border-t border-outline-gray-2 text-ink-gray-5 text-sm text-center">
        No results to display
      </p>

      <template v-else>
        <Table class="px-4" height="max-h-96" :columns="columns" :rows="rows">
          <template #database="{ row }">{{ siteLabel(row.database) }}</template>
          <template #percent="{ row }">{{ Number(row.percent).toFixed(1) }}%</template>
          <template #calls="{ row }">{{ formatCount(row.calls) }}</template>
          <template #rows_examined="{ row }">{{ formatCount(row.rows_examined) }}</template>
          <template #rows_sent="{ row }">{{ formatCount(row.rows_sent) }}</template>
          <template #average_time_ms="{ row }">
            {{ formatMilliseconds(row.average_time_ms) }}
          </template>
          <template #query="{ row }">
            <button
              type="button"
              class="max-w-md text-left truncate"
              @click="openQuery = row.query"
            >
              {{ row.query }}
            </button>
          </template>
        </Table>

        <div class="flex justify-end px-4 pb-4">
          <Button v-if="hasNextPage" :loading="loading" @click="load(rows.length)">
            Load more
          </Button>
        </div>
      </template>
    </template>
  </DatabasePanel>

  <Dialog
    :model-value="Boolean(openQuery)"
    title="Query"
    size="2xl"
    @update:model-value="openQuery = ''"
  >
    <pre
      class="bg-surface-gray-2 p-3 border rounded-6 border-outline-gray-1 text-ink-gray-7 text-sm whitespace-pre-wrap"
      >{{ openQuery }}</pre
    >
  </Dialog>
</template>
