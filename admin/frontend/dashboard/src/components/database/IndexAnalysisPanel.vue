<script setup lang="ts">
import { Button, ErrorMessage, TabButtons } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

import EmptyState from '@/components/common/EmptyState.vue'
import Table from '@/components/common/Table.vue'
import DatabasePanel from '@/components/database/DatabasePanel.vue'
import PerformanceSchemaNotice from '@/components/database/PerformanceSchemaNotice.vue'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { databaseApi } from '@/api/database'
import type { PerformanceSection } from '@/types/database'
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

const tab = ref('unused_indexes')

const rows = ref<PerformanceRow[]>([])
const hasNextPage = ref(false)
const loading = ref(false)
const error = ref('')
const loaded = ref(false)

const siteLabel = (database: string | null) =>
  props.siteByDatabase[database ?? ''] || database || '—'

const withSite = (columns: Column[]) =>
  props.showSite ? [{ key: 'database', label: 'Site' }, ...columns] : columns

const tabOptions = [
  { label: 'Unused', value: 'unused_indexes' },
  { label: 'Redundant', value: 'redundant_indexes' },
]

const columnsByTab: Record<string, Column[]> = {
  unused_indexes: [
    { key: 'table', label: 'Table Name' },
    { key: 'index', label: 'Index Name' },
  ],
  redundant_indexes: [
    { key: 'table', label: 'Table Name' },
    { key: 'dominant_index', label: 'Dominant Index' },
    { key: 'dominant_index_columns', label: 'Dominant Index Columns' },
    { key: 'redundant_index', label: 'Redundant Index' },
    { key: 'redundant_index_columns', label: 'Redundant Index Columns' },
  ],
}

const columns = computed(() => withSite(columnsByTab[tab.value]))

// Redundant indexes come from information_schema, so they read fine with
// Performance Schema off; unused indexes do not.
const needsPerformanceSchema = computed(() => tab.value === 'unused_indexes')

const load = async (offset = 0) => {
  loading.value = true
  error.value = ''
  try {
    const result = await databaseApi.performanceReport(tab.value, props.site, pageSize, offset)
    if (hasApiError(result)) {
      throw new Error(apiErrorMessage(result, 'Could not load the index report.'))
    }

    rows.value = offset ? [...rows.value, ...result.data] : result.data
    hasNextPage.value = result.has_next_page
    loaded.value = true
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not load the index report.')
  } finally {
    loading.value = false
  }
}

watch(tab, () => load())

watch(
  () => props.site,
  () => loaded.value && load(),
)
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

    <template v-else>
      <PerformanceSchemaNotice v-if="needsPerformanceSchema && !enabled" />

      <EmptyState
        v-else-if="!rows.length"
        icon="lucide-list-tree"
        :title="tab === 'redundant_indexes' ? 'No redundant indexes' : 'No unused indexes'"
        :description="tab === 'redundant_indexes' ? 'No index repeats another' : 'Every index has been used'"
        :bordered="false"
        class="flex-1 justify-center pb-16"
      />

      <template v-else>
        <Table height="flex-1" :columns="columns" :rows="rows">
          <template #database="{ row }">{{ siteLabel(row.database) }}</template>
        </Table>

        <div class="flex justify-end">
          <Button v-if="hasNextPage" :loading="loading" @click="load(rows.length)">
            Load more
          </Button>
        </div>
      </template>
    </template>
  </DatabasePanel>
</template>
