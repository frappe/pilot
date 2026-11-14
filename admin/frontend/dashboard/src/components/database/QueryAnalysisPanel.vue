<script setup lang="ts">
import { Button, Dialog, ErrorMessage, TabButtons } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

import EmptyState from '@/components/common/EmptyState.vue'
import Table from '@/components/common/Table.vue'
import DatabasePanel from '@/components/database/DatabasePanel.vue'
import PerformanceSchemaNotice from '@/components/database/PerformanceSchemaNotice.vue'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { databaseApi } from '@/api/database'
import { formatCount, formatMilliseconds } from '@/utils/format'
import type { PerformanceSection, SlowQueryReport } from '@/types/database'
import { errorMessage } from '@/utils/error'

interface Props {
  active?: boolean
  site?: string
  badge?: string
  enabled?: boolean
  showSite?: boolean
  siteByDatabase?: Record<string, string>
}

interface Column {
  key: string
  label: string
}

type PerformanceRow = PerformanceSection['data'][number]

const props = withDefaults(defineProps<Props>(), {
  active: false,
  site: '',
  badge: '',
  enabled: false,
  showSite: false,
  siteByDatabase: () => ({}),
})

const pageSize = 20

const tab = ref('slow_queries')

const rows = ref<PerformanceRow[]>([])
const slowReport = ref<SlowQueryReport | null>(null)
const hasNextPage = ref(false)
const loading = ref(false)
const error = ref('')
const loaded = ref(false)

const siteLabel = (database: string | null) =>
  props.siteByDatabase[database ?? ''] || database || '—'

const withSite = (columns: Column[]) =>
  props.showSite ? [{ key: 'database', label: 'Site' }, ...columns] : columns

const percentOf = (row: PerformanceRow) => ('percent' in row ? row.percent : 0)
const callsOf = (row: PerformanceRow) => ('calls' in row ? row.calls : 0)
const rowsSentOf = (row: PerformanceRow) => ('rows_sent' in row ? row.rows_sent : 0)
const rowsExaminedOf = (row: PerformanceRow) => ('rows_examined' in row ? row.rows_examined : 0)
const averageTimeOf = (row: PerformanceRow) => ('average_time_ms' in row ? row.average_time_ms : 0)
const queryOf = (row: PerformanceRow) => ('query' in row ? (row.query ?? '') : '')

const tabOptions = [
  { label: 'Slow queries', value: 'slow_queries' },
  { label: 'Time consuming', value: 'time_consuming_queries' },
  { label: 'Full table scans', value: 'full_table_scan_queries' },
]

const columnsByTab: Record<string, Column[]> = {
  slow_queries: [
    { key: 'rows_examined', label: 'Rows Examined' },
    { key: 'rows_sent', label: 'Rows Sent' },
    { key: 'calls', label: 'Calls' },
    { key: 'duration_seconds', label: 'Duration' },
    { key: 'query', label: 'Query' },
  ],
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

const isSlowTab = computed(() => tab.value === 'slow_queries')

const slowEmptyState = computed(() =>
  slowReport.value?.log_path
    ? {
        title: 'No slow queries',
        description: `None over ${slowReport.value.long_query_time}s in the recent slow log`,
      }
    : {
        title: 'Slow query log is off',
        description: 'Restart the database so MariaDB writes slow queries to a file Pilot can read.',
      },
)

const loadSlowQueries = async () => {
  const result = await databaseApi.slowQueries(props.site)
  if (hasApiError(result)) throw new Error(apiErrorMessage(result, 'Could not load slow queries.'))
  slowReport.value = result
  loaded.value = true
}

const load = async (offset = 0) => {
  loading.value = true
  error.value = ''
  try {
    if (isSlowTab.value) return await loadSlowQueries()
    const result = await databaseApi.performanceReport(tab.value, props.site, pageSize, offset)
    if (hasApiError(result)) {
      throw new Error(apiErrorMessage(result, 'Could not load the query report.'))
    }

    rows.value = offset ? [...rows.value, ...result.data] : result.data
    hasNextPage.value = result.has_next_page
    loaded.value = true
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not load the query report.')
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
    :active="active"
    :badge="badge"
    :loading="loading"
    @open="load()"
    @refresh="load()"
  >
    <template #start>
      <TabButtons v-model="tab" :options="tabOptions" size="sm" />
    </template>

    <ErrorMessage v-if="error" :message="error" />

    <template v-else-if="isSlowTab">
      <Table v-if="slowReport?.queries.length" height="flex-1" :columns="columns" :rows="slowReport.queries">
        <template #database="{ row }">{{ siteLabel(row.database) }}</template>
        <template #rows_examined="{ row }">{{ formatCount(row.rows_examined) }}</template>
        <template #rows_sent="{ row }">{{ formatCount(row.rows_sent) }}</template>
        <template #calls="{ row }">{{ formatCount(row.calls) }}</template>
        <template #duration_seconds="{ row }">
          {{ formatMilliseconds(row.duration_seconds * 1000) }}
        </template>
        <template #query="{ row }">
          <button type="button" class="max-w-md text-left truncate" @click="openQuery = row.example">
            {{ row.query }}
          </button>
        </template>
      </Table>

      <EmptyState
        v-else
        icon="lucide-turtle"
        :title="slowEmptyState.title"
        :description="slowEmptyState.description"
        :bordered="false"
        class="flex-1 justify-center pb-16"
      />
    </template>

    <template v-else>
      <PerformanceSchemaNotice v-if="!enabled" />

      <EmptyState
        v-else-if="!rows.length"
        icon="lucide-scan-search"
        :title="tab === 'full_table_scan_queries' ? 'No full table scans' : 'No queries yet'"
        :description="tab === 'full_table_scan_queries' ? 'No query has read a whole table' : 'Nothing recorded since the server started'"
        :bordered="false"
        class="flex-1 justify-center pb-16"
      />

      <template v-else>
        <Table height="flex-1" :columns="columns" :rows="rows">
          <template #database="{ row }">{{ siteLabel(row.database) }}</template>
          <template #percent="{ row }">{{ percentOf(row).toFixed(1) }}%</template>
          <template #calls="{ row }">{{ formatCount(callsOf(row)) }}</template>
          <template #rows_examined="{ row }">{{ formatCount(rowsExaminedOf(row)) }}</template>
          <template #rows_sent="{ row }">{{ formatCount(rowsSentOf(row)) }}</template>
          <template #average_time_ms="{ row }">
            {{ formatMilliseconds(averageTimeOf(row)) }}
          </template>
          <template #query="{ row }">
            <button
              type="button"
              class="max-w-md text-left truncate"
              @click="openQuery = queryOf(row)"
            >
              {{ queryOf(row) }}
            </button>
          </template>
        </Table>

        <div class="flex justify-end">
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
